import contextlib
import dataclasses
import functools
import inspect
import re
import types
from collections.abc import Callable, Coroutine, Iterator
from http import HTTPStatus
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Self,
    Union,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)
from urllib.parse import parse_qs

import msgspec

from .logs import log
from .types import RSGIScope

if TYPE_CHECKING:
    from granian._granian import RSGIHTTPProtocol

__all__ = (
    "Body",
    "Depends",
    "Handler",
    "Query",
    "Response",
    "Router",
)

type Handler = Callable[..., Coroutine[Any, Any, "Response | Any | None"]]


class _QueryMeta:
    """Marker attached via Annotated to identify query-string parameters."""


class _BodyMeta:
    """Marker attached via Annotated to identify JSON body parameters."""


if TYPE_CHECKING:
    # At type-check time Query[T] and Body[T] resolve to T so pyright
    # sees the concrete struct/TypedDict type on handler parameters.
    type Query[T] = T
    type Body[T] = T
else:

    class Query:
        """Annotates a handler parameter as a query-string struct.

        Usage: ``async def handler(query: Query[MyStruct]) -> dict: ...``
        Produces ``Annotated[MyStruct, _QueryMeta()]`` so the router can detect it.
        """

        def __class_getitem__(cls, item):
            return Annotated[item, _QueryMeta()]

    class Body:
        """Annotates a handler parameter as a JSON request body.

        Usage: ``async def handler(req: Body[GenerateRequest]) -> Result: ...``
        Produces ``Annotated[GenerateRequest, _BodyMeta()]`` so the router
        can detect it.
        """

        def __class_getitem__(cls, item):
            return Annotated[item, _BodyMeta()]


class Depends:
    """Marks a handler parameter as a dependency resolved by the router.

    The wrapped callable can be a regular function, a coroutine function,
    or an async generator (treated as a context manager for setup/teardown).

    Usage: ``conn: Annotated[Connection, Depends(get_conn)]``
    """

    __slots__ = ("func",)

    def __init__(self, func: Callable[..., Any]) -> None:
        self.func = func


_encoder = msgspec.json.Encoder()


class Response(msgspec.Struct):
    """Handler return type that decouples handlers from the RSGI protocol.
    If body is bytes it's passed through as-is, otherwise it's JSON-encoded.
    """

    body: Any
    status: int = HTTPStatus.OK
    content_type: str = "application/json"


def _send(proto: "RSGIHTTPProtocol", response: Response) -> None:
    """Serialize a Response and write it to the RSGI protocol."""
    if isinstance(response.body, bytes):
        raw = response.body
    else:
        raw = _encoder.encode(response.body)
    headers = [("content-type", response.content_type)]
    proto.response_bytes(response.status, headers, raw)


def _error(proto: "RSGIHTTPProtocol", status: int, message: str) -> None:
    """Send a JSON error response with the given status and message."""
    _send(proto, Response({"error": message}, status=status))


# Matches {param} placeholders in route patterns like "/users/{id}"
_PARAM_RE = re.compile(r"\{(\w+)\}")


def _unwrap_return_type(hint: Any) -> type | None:
    """Extract a concrete return type from the handler's annotation,
    filtering out ``Response`` and ``None`` from unions.
    """
    # get_type_hints resolves a bare `-> None` to NoneType
    if hint is None or hint is type(None) or hint is inspect.Parameter.empty:
        return None
    origin = get_origin(hint)
    if origin is types.UnionType or origin is Union:
        args = [a for a in get_args(hint) if a is not Response and a is not type(None)]
        if len(args) == 1:
            return args[0]
        return None
    if hint is Response:
        return None
    return hint


@dataclasses.dataclass(slots=True)
class HandlerSpec:
    """Injection metadata for a handler, detected at registration time."""

    query_param: str | None = None
    query_type: type[msgspec.Struct] | None = None
    body_param: str | None = None
    body_type: type | None = None
    return_type: type | None = None
    deps: dict[str, Depends] = dataclasses.field(default_factory=dict)

    @property
    def has_injections(self) -> bool:
        return (
            self.query_type is not None or self.body_type is not None or bool(self.deps)
        )


def _inspect_handler(handler: Handler) -> HandlerSpec:
    """Inspect handler annotations for Query[T], Body[T], and Depends markers."""
    hints = get_type_hints(handler, include_extras=True)
    spec = HandlerSpec()

    ret = hints.get("return")
    spec.return_type = _unwrap_return_type(ret)

    for name, hint in hints.items():
        if name == "return":
            continue
        if get_origin(hint) is not Annotated:
            continue
        inner, *meta = get_args(hint)
        for m in meta:
            if isinstance(m, _QueryMeta):
                spec.query_param = name
                spec.query_type = inner
            elif isinstance(m, _BodyMeta):
                spec.body_param = name
                spec.body_type = inner
            elif isinstance(m, Depends):
                spec.deps[name] = m
    return spec


@functools.cache
def _sequence_fields(struct_type: type[msgspec.Struct]) -> frozenset[str]:
    """Names of struct fields typed as a sequence (kept as lists when parsing)."""
    return frozenset(
        f.name
        for f in msgspec.structs.fields(struct_type)
        if get_origin(f.type) in (list, set, frozenset, tuple)
    )


def _parse_query(qs: str, struct_type: type[msgspec.Struct]) -> msgspec.Struct:
    """Parse a query string into a msgspec.Struct using lenient coercion."""
    raw = parse_qs(qs, keep_blank_values=True)
    seq_fields = _sequence_fields(struct_type)
    flat = {k: v if k in seq_fields else v[-1] for k, v in raw.items()}
    return msgspec.convert(flat, struct_type, strict=False)


async def _call_handler(
    handler: Handler,
    spec: HandlerSpec,
    scope: RSGIScope,
    proto: "RSGIHTTPProtocol",
    path_params: dict[str, str] | None = None,
) -> Any:
    """Resolve injections and call the handler."""
    if not spec.has_injections:
        if path_params:
            return await handler(scope, proto, **path_params)
        return await handler(scope, proto)

    kwargs: dict[str, Any] = {}
    if path_params:
        kwargs.update(path_params)
    if spec.query_type is not None:
        if spec.query_param is None:
            raise RuntimeError("query_type set without query_param")
        kwargs[spec.query_param] = _parse_query(scope.query_string, spec.query_type)
    if spec.body_type is not None:
        if spec.body_param is None:
            raise RuntimeError("body_type set without body_param")
        raw = await proto()
        kwargs[spec.body_param] = msgspec.json.decode(raw, type=spec.body_type)

    if not spec.deps:
        return await handler(**kwargs)

    async with contextlib.AsyncExitStack() as stack:
        for name, dep in spec.deps.items():
            if inspect.isasyncgenfunction(dep.func):
                cm = contextlib.asynccontextmanager(dep.func)()
                kwargs[name] = await stack.enter_async_context(cm)
            elif inspect.iscoroutinefunction(dep.func):
                kwargs[name] = await dep.func()
            else:
                kwargs[name] = dep.func()
        return await handler(**kwargs)


@dataclasses.dataclass
class Route:
    """A single route entry. Static patterns (e.g. "/health") are matched by
    exact string comparison; parametric patterns (e.g. "/users/{id}") are
    compiled into a regex at init time.
    """

    method: str
    """HTTP method (GET, POST, ...)."""
    pattern: str
    """URL pattern, e.g. "/users/{id}"."""
    handler: Handler
    """Async callable invoked on match."""
    spec: HandlerSpec = dataclasses.field(default_factory=HandlerSpec)
    """Injection metadata detected from handler annotations."""
    param_names: list[str] = dataclasses.field(init=False)
    """Parameter names extracted from the pattern."""
    _regex: re.Pattern[str] | None = dataclasses.field(init=False)
    """Compiled regex for parametric patterns, None for static ones."""

    def __post_init__(self) -> None:
        self.param_names = _PARAM_RE.findall(self.pattern)
        if self.param_names:
            # split alternates static text and param names; escape the static parts
            parts = _PARAM_RE.split(self.pattern)
            regex = "".join(
                f"(?P<{part}>[^/]+)" if i % 2 else re.escape(part)
                for i, part in enumerate(parts)
            )
            self._regex = re.compile(f"^{regex}$")
        else:
            self._regex = None

    def match(self, path: str) -> dict[str, str] | None:
        """Return captured path params on match, or None on mismatch."""
        if not self.param_names:
            return {} if path == self.pattern else None
        if self._regex is None:
            raise RuntimeError("parametric route without compiled regex")
        m = self._regex.match(path)
        return m.groupdict() if m else None


@dataclasses.dataclass
class Router:
    """Minimal HTTP router with static and parametric route matching."""

    _routes: list[Route] = dataclasses.field(default_factory=list)
    """Parametric routes tested via regex."""
    _static: dict[tuple[str, str], tuple[Handler, HandlerSpec]] = dataclasses.field(
        default_factory=dict
    )
    """Static (method, path) → (handler, spec) lookup for O(1) dispatch."""

    @overload
    def add(self, method: str, path: str, handler: Handler) -> None: ...
    @overload
    def add(self, method: str, path: str) -> Callable[[Handler], Handler]: ...
    def add(
        self, method: str, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        def register(h: Handler) -> Handler:
            spec = _inspect_handler(h)
            if "{" in path:
                self._routes.append(Route(method, path, h, spec))
            else:
                self._static[(method, path)] = (h, spec)
            return h

        if handler is not None:
            register(handler)
            return None
        return register

    @overload
    def get(self, path: str, handler: Handler) -> None: ...
    @overload
    def get(self, path: str) -> Callable[[Handler], Handler]: ...
    def get(
        self, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        return self.add("GET", path, handler)  # type: ignore[call-overload]

    @overload
    def post(self, path: str, handler: Handler) -> None: ...
    @overload
    def post(self, path: str) -> Callable[[Handler], Handler]: ...
    def post(
        self, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        return self.add("POST", path, handler)  # type: ignore[call-overload]

    @overload
    def put(self, path: str, handler: Handler) -> None: ...
    @overload
    def put(self, path: str) -> Callable[[Handler], Handler]: ...
    def put(
        self, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        return self.add("PUT", path, handler)  # type: ignore[call-overload]

    @overload
    def patch(self, path: str, handler: Handler) -> None: ...
    @overload
    def patch(self, path: str) -> Callable[[Handler], Handler]: ...
    def patch(
        self, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        return self.add("PATCH", path, handler)  # type: ignore[call-overload]

    @overload
    def delete(self, path: str, handler: Handler) -> None: ...
    @overload
    def delete(self, path: str) -> Callable[[Handler], Handler]: ...
    def delete(
        self, path: str, handler: Handler | None = None
    ) -> Callable[[Handler], Handler] | None:
        return self.add("DELETE", path, handler)  # type: ignore[call-overload]

    def schema_types(self) -> list[type[msgspec.Struct]]:
        """Return all unique query Struct types registered across routes."""
        seen: set[type[msgspec.Struct]] = set()
        for _, spec in self._static.values():
            if spec.query_type is not None:
                seen.add(spec.query_type)
        for route in self._routes:
            if route.spec.query_type is not None:
                seen.add(route.spec.query_type)
        return list(seen)

    def iter_routes(self) -> Iterator[tuple[str, str, Handler, HandlerSpec]]:
        """Yield ``(method, path, handler, spec)`` for every registered route."""
        for (method, path), (handler, spec) in self._static.items():
            yield method, path, handler, spec
        for route in self._routes:
            yield route.method, route.pattern, route.handler, route.spec

    def include(self, other: Self, prefix: str = "") -> None:
        """Merge all routes from another router, optionally under a URL prefix."""
        for (method, path), entry in other._static.items():
            self._static[(method, prefix + path)] = entry
        for route in other._routes:
            self._routes.append(
                Route(route.method, prefix + route.pattern, route.handler, route.spec)
            )

    def _handle_result(
        self, proto: "RSGIHTTPProtocol", result: Response | Any | None
    ) -> None:
        """Normalize a handler return value and send it. None is a no-op,
        letting streaming handlers drive the protocol themselves."""
        if result is None:
            return
        if not isinstance(result, Response):
            result = Response(result)
        _send(proto, result)

    async def dispatch(self, scope: RSGIScope, proto: "RSGIHTTPProtocol") -> None:
        """Route an incoming request to the matching handler and send the response."""
        method = scope.method
        path = scope.path

        # Fast path: static routes
        entry = self._static.get((method, path))
        if entry is not None:
            handler, spec = entry
            try:
                if spec.has_injections:
                    result = await _call_handler(handler, spec, scope, proto)
                else:
                    result = await handler(scope, proto)
                self._handle_result(proto, result)
            # DecodeError also covers ValidationError (its subclass)
            except msgspec.DecodeError as exc:
                _error(proto, HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except Exception:
                log.exception("Handler error: %s %s", method, path)
                _error(proto, HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return

        # Parametric routes
        allowed_methods: set[str] = set()
        for route in self._routes:
            params = route.match(path)
            if params is None:
                continue
            if route.method != method:
                allowed_methods.add(route.method)
                continue
            try:
                if route.spec.has_injections:
                    result = await _call_handler(
                        route.handler, route.spec, scope, proto, params
                    )
                else:
                    result = await route.handler(scope, proto, **params)
                self._handle_result(proto, result)
            except msgspec.DecodeError as exc:
                _error(proto, HTTPStatus.UNPROCESSABLE_ENTITY, str(exc))
            except Exception:
                log.exception("Handler error: %s %s", method, path)
                _error(proto, HTTPStatus.INTERNAL_SERVER_ERROR, "internal server error")
            return

        # Static routes matching the path under other methods
        allowed_methods.update(m for m, p in self._static if p == path)

        if allowed_methods:
            raw = _encoder.encode({"error": "method not allowed"})
            headers = [
                ("content-type", "application/json"),
                ("allow", ", ".join(sorted(allowed_methods))),
            ]
            proto.response_bytes(HTTPStatus.METHOD_NOT_ALLOWED, headers, raw)
        else:
            _error(proto, HTTPStatus.NOT_FOUND, "not found")

from collections.abc import Iterable
from typing import Any

import msgspec

from .router import Handler, HandlerSpec, Response, Router

__all__ = ("build_openapi", "openapi_handler")


def _schema_ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def _build_query_params(spec: HandlerSpec) -> list[dict[str, Any]]:
    """Generate OpenAPI parameters from a query struct type."""
    if spec.query_type is None:
        return []

    # schema_components returns ($refs, components); the real schema lives in
    # the components map (msgspec >= 0.21 never inlines it)
    _, components = msgspec.json.schema_components(
        [spec.query_type], ref_template="#/components/schemas/{name}"
    )
    name = getattr(spec.query_type, "__name__", "")
    schema = components.get(name) or next(iter(components.values()), {})
    props = schema.get("properties", {})
    required_set = set(schema.get("required", []))
    params: list[dict[str, Any]] = []

    for name, prop in props.items():
        params.append(
            {
                "name": name,
                "in": "query",
                "required": name in required_set,
                "schema": prop,
            }
        )
    return params


def _build_request_body(type_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": _schema_ref(type_name),
            }
        },
    }


def _build_responses(spec: HandlerSpec) -> dict[str, Any]:
    """Generate OpenAPI responses from the handler's return type."""
    responses: dict[str, Any] = {}
    if spec.return_type is not None:
        name = getattr(spec.return_type, "__name__", None)
        if name:
            responses["200"] = {
                "description": "Successful response",
                "content": {
                    "application/json": {
                        "schema": _schema_ref(name),
                    }
                },
            }
        else:
            responses["200"] = {"description": "Successful response"}
    else:
        responses["200"] = {"description": "OK"}

    if spec.body_type is not None:
        responses["422"] = {"description": "Validation error"}

    return responses


def build_openapi(
    router: Router,
    *,
    title: str,
    version: str = "0.1.0",
    exclude_paths: Iterable[str] = ("/docs", "/redoc", "/openapi.json"),
) -> bytes:
    """Build an OpenAPI 3.1 spec (JSON bytes) from a router's registered routes.

    Operation summaries come from the first docstring line of each handler
    (a leading "METHOD /path — " prefix is stripped); schemas are generated
    with msgspec from Query/Body/return annotations.
    """
    excluded = frozenset(exclude_paths)
    paths: dict[str, dict[str, Any]] = {}
    all_types: list[type] = []

    for method, path, handler, spec in router.iter_routes():
        if path in excluded:
            continue

        operation: dict[str, Any] = {}

        doc = handler.__doc__
        if doc:
            first_line = doc.strip().split("\n")[0]
            if " — " in first_line:
                first_line = first_line.split(" — ", 1)[1]
            operation["summary"] = first_line

        params = _build_query_params(spec)
        if params:
            operation["parameters"] = params

        if spec.body_type is not None:
            type_name = (
                getattr(spec.body_type, "__name__", None)
                or spec.body_type.__class__.__name__
            )
            operation["requestBody"] = _build_request_body(type_name)
            all_types.append(spec.body_type)

        operation["responses"] = _build_responses(spec)
        if spec.return_type is not None:
            all_types.append(spec.return_type)

        if spec.query_type is not None:
            all_types.append(spec.query_type)

        paths.setdefault(path, {})[method.lower()] = operation

    seen: set[int] = set()
    unique_types: list[type] = []
    for t in all_types:
        if id(t) not in seen:
            seen.add(id(t))
            unique_types.append(t)

    components: dict[str, Any] = {}
    if unique_types:
        _, schemas = msgspec.json.schema_components(
            unique_types, ref_template="#/components/schemas/{name}"
        )
        components["schemas"] = schemas

    spec_dict: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version},
        "paths": paths,
    }
    if components:
        spec_dict["components"] = components

    return msgspec.json.encode(spec_dict)


def openapi_handler(router: Router, *, title: str, version: str = "0.1.0") -> Handler:
    """Build a GET handler serving the spec, built lazily once and cached."""
    spec_bytes: bytes | None = None

    async def handle_openapi(scope, proto) -> Response:
        """Serve the OpenAPI spec."""
        nonlocal spec_bytes
        if spec_bytes is None:
            spec_bytes = build_openapi(router, title=title, version=version)
        return Response(spec_bytes)

    return handle_openapi

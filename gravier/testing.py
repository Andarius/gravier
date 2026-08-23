import dataclasses
from collections.abc import MutableMapping
from typing import Any

from .router import Router

__all__ = ("ASGIBridge", "FakeProto", "FakeScope", "FakeStreamTransport")


@dataclasses.dataclass
class FakeScope:
    """Stand-in for granian's RSGIHTTPScope in handler unit tests."""

    method: str
    path: str
    query_string: str = ""
    proto: str = "http"
    headers: MutableMapping[str, str] = dataclasses.field(default_factory=dict)
    scheme: str = "http"
    http_version: str = "1.1"
    server: str = "127.0.0.1:0"
    authority: str = "127.0.0.1:0"
    client: str = "127.0.0.1:0"


class FakeStreamTransport:
    """Captures chunks written through ``response_stream``."""

    __slots__ = ("chunks",)

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.chunks.append(data)

    async def send_str(self, data: str) -> None:
        self.chunks.append(data.encode())


class FakeProto:
    """Captures the response written to the RSGI protocol.

    ``await proto()`` returns the request body (for ``Body[T]`` handlers);
    ``response_stream`` returns a FakeStreamTransport whose chunks are
    exposed on ``stream`` after the handler ran.
    """

    __slots__ = ("status", "headers", "body", "stream", "_request_body")

    def __init__(self, request_body: bytes = b"") -> None:
        self.status: int = 0
        self.headers: list[tuple[str, str]] = []
        self.body: bytes = b""
        self.stream: FakeStreamTransport | None = None
        self._request_body = request_body

    async def __call__(self) -> bytes:
        return self._request_body

    def response_bytes(
        self, status: int, headers: list[tuple[str, str]], body: bytes
    ) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def response_str(
        self, status: int, headers: list[tuple[str, str]], body: str
    ) -> None:
        self.response_bytes(status, headers, body.encode())

    def response_empty(self, status: int, headers: list[tuple[str, str]]) -> None:
        self.response_bytes(status, headers, b"")

    def response_stream(
        self, status: int, headers: list[tuple[str, str]]
    ) -> FakeStreamTransport:
        self.status = status
        self.headers = headers
        self.stream = FakeStreamTransport()
        return self.stream


class ASGIBridge:
    """Thin ASGI wrapper around a Router for in-process HTTP testing.

    Lets any ASGI test client (e.g. ``niquests.Session(app=...)``) exercise
    RSGI handlers without booting granian. Streaming responses are collected
    by FakeProto and sent as one body once the handler returns.
    """

    def __init__(self, router: Router) -> None:
        self.router = router

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Any, send: Any
    ) -> None:
        if scope["type"] == "lifespan":
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            return

        request_body = b""
        msg = await receive()
        if msg.get("type") == "http.request":
            request_body = msg.get("body", b"")

        rsgi_scope = FakeScope(
            method=scope["method"],
            path=scope["path"],
            query_string=(scope.get("query_string") or b"").decode(),
        )
        proto = FakeProto(request_body)
        await self.router.dispatch(rsgi_scope, proto)  # type: ignore[arg-type]

        body = proto.body
        if proto.stream is not None:
            body = b"".join(proto.stream.chunks)
        await send(
            {
                "type": "http.response.start",
                "status": proto.status,
                "headers": [(k.encode(), v.encode()) for k, v in proto.headers],
            }
        )
        await send({"type": "http.response.body", "body": body})

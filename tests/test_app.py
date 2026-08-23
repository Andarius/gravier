import asyncio
import json

from gravier import App, Router
from gravier.testing import FakeProto, FakeScope


def test_lifespan_hooks_run_in_order_and_dispatch_works():
    events: list[str] = []

    async def open_db() -> None:
        events.append("open")

    async def close_db() -> None:
        events.append("close")

    router = Router()

    @router.get("/health")
    async def _health(scope, proto) -> dict:
        return {"status": "ok"}

    app = App(router, on_startup=[open_db], on_shutdown=[close_db])

    loop = asyncio.new_event_loop()
    try:
        app.__rsgi_init__(loop)
        proto = FakeProto()
        loop.run_until_complete(
            app.__rsgi__(FakeScope(method="GET", path="/health"), proto)  # type: ignore[arg-type]
        )
        app.__rsgi_del__(loop)
    finally:
        loop.close()

    assert events == ["open", "close"]
    assert json.loads(proto.body) == {"status": "ok"}


class FakeWsProto:
    """Minimal RSGIWebsocketProtocol stand-in recording close() calls."""

    def __init__(self) -> None:
        self.closed_with: list[int | None] = []

    def close(self, status: int | None) -> tuple[int, bool]:
        self.closed_with.append(status)
        return (403, False)


def test_ws_scope_rejected_via_close():
    app = App(Router())
    loop = asyncio.new_event_loop()
    try:
        proto = FakeWsProto()
        loop.run_until_complete(
            app.__rsgi__(FakeScope(method="GET", path="/ws", proto="ws"), proto)  # type: ignore[arg-type]
        )
    finally:
        loop.close()
    assert proto.closed_with == [None]

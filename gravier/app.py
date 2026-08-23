import dataclasses
from collections.abc import Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any

from .logs import log
from .router import Router
from .types import RSGIScope

if TYPE_CHECKING:
    import asyncio

    from granian._granian import RSGIHTTPProtocol

__all__ = ("App",)

type LifespanHook = Callable[[], Coroutine[Any, Any, None]]


@dataclasses.dataclass
class App:
    """RSGI application: a Router plus startup/shutdown hooks.

    Granian calls ``__rsgi_init__`` once per worker before serving and
    ``__rsgi_del__`` on shutdown; hooks run in registration order on the
    worker's event loop.
    """

    router: Router
    on_startup: Sequence[LifespanHook] = ()
    on_shutdown: Sequence[LifespanHook] = ()

    def __rsgi_init__(self, loop: "asyncio.AbstractEventLoop") -> None:
        for hook in self.on_startup:
            loop.run_until_complete(hook())
        log.info("app initialized")

    async def __rsgi__(self, scope: RSGIScope, proto: "RSGIHTTPProtocol") -> None:
        if scope.proto != "http":
            proto.response_empty(400, [])
            return
        await self.router.dispatch(scope, proto)

    def __rsgi_del__(self, loop: "asyncio.AbstractEventLoop") -> None:
        for hook in self.on_shutdown:
            loop.run_until_complete(hook())
        log.info("app closed")

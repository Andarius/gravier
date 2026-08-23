# Target app for the granian e2e smoke (not collected by pytest).
import msgspec

from gravier import SSE_HEADERS, App, Body, Query, Router, openapi_handler, sse


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10


class CreateItem(msgspec.Struct):
    name: str
    price: float


router = Router()
events: list[str] = []


async def _startup() -> None:
    events.append("up")


@router.get("/health")
async def health(scope, proto) -> dict:
    return {"status": "ok", "events": events}


@router.get("/search")
async def search(query: Query[SearchQuery]) -> dict:
    return {"q": query.q, "limit": query.limit}


@router.post("/items")
async def create(req: Body[CreateItem]) -> dict:
    return {"name": req.name}


@router.post("/events")
async def stream(scope, proto) -> None:
    transport = proto.response_stream(200, SSE_HEADERS)
    await transport.send_bytes(sse("delta", {"n": 1}))
    await transport.send_bytes(sse("done", {"n": 2}))


router.get("/openapi.json", openapi_handler(router, title="e2e"))

app = App(router, on_startup=[_startup])

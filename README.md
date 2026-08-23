# Gravier

Small typed wrapper around [granian](https://github.com/emmett-framework/granian)'s RSGI interface: a router with msgspec validation, app lifecycle, SSE helpers, OpenAPI generation, and test utilities.

```bash
pip install gravier
```

## Quick start

```python
import msgspec

from gravier import App, Body, Query, Response, Router, serve


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10


class CreateItem(msgspec.Struct):
    name: str
    price: float


router = Router()


@router.get("/health")
async def health(scope, proto) -> dict:
    return {"status": "ok"}


@router.get("/items/{item_id}")
async def get_item(scope, proto, item_id: str) -> dict:
    return {"item_id": item_id}


@router.get("/search")
async def search(query: Query[SearchQuery]) -> dict:
    return {"q": query.q, "limit": query.limit}


@router.post("/items")
async def create(req: Body[CreateItem]) -> dict:  # 422 on invalid body
    return {"name": req.name}


app = App(router, on_startup=[], on_shutdown=[])
```

Serve it (`main.py` above):

```bash
granian --interface rsgi main:app
```

or programmatically, with a clean error when the port is taken:

```python
from gravier import serve

serve("main:app", port=8000)
```

## Features

- **Router** — static and `{param}` routes, `GET/POST/PUT/PATCH/DELETE`, `include(other, prefix=...)`, JSON errors for 404/405/422/500.
- **`Query[T]` / `Body[T]`** — msgspec-validated query strings and JSON bodies; pyright sees the concrete `T` on the parameter. Invalid input returns 422 automatically.
- **`Depends`** — inject sync callables, coroutines, or async generators (setup/teardown) into handlers.
- **`App`** — bundles a router with startup/shutdown hooks mapped onto granian's `__rsgi_init__` / `__rsgi_del__` worker lifecycle.
- **SSE** — `sse(event, data)` formatting and ready-made `SSE_HEADERS`; a handler returning `None` keeps the protocol to itself:

  ```python
  from gravier import SSE_HEADERS, sse

  @router.post("/events")
  async def events(scope, proto) -> None:
      transport = proto.response_stream(200, SSE_HEADERS)
      await transport.send_bytes(sse("delta", {"text": "hello"}))
  ```

- **OpenAPI** — `build_openapi(router, title=...)` generates a 3.1 spec from `Query`/`Body`/return annotations; `openapi_handler(router, title=...)` serves it.
- **Testing** (`gravier.testing`) — `FakeScope`/`FakeProto` for direct handler tests (including captured streams), and `ASGIBridge` to run the router under any ASGI test client without booting granian:

  ```python
  import niquests
  from gravier.testing import ASGIBridge

  with niquests.Session(app=ASGIBridge(router)) as client:
      assert client.get("http://test/health").json() == {"status": "ok"}
  ```

## Development

```bash
uv sync --group dev
just ci   # lint + type check + tests
```

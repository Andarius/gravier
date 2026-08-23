import json
from typing import Annotated, Any

import msgspec
import pytest

from gravier import Body, Depends, Query, Response, Router
from gravier.testing import FakeProto, FakeScope


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10


class CreateItem(msgspec.Struct):
    name: str
    price: float


def make_router() -> Router:
    router = Router()

    @router.get("/health")
    async def _health(scope, proto) -> dict:
        return {"status": "ok"}

    @router.get("/items/{item_id}")
    async def _get_item(scope, proto, item_id: str) -> dict:
        return {"item_id": item_id}

    @router.get("/search")
    async def _search(query: Query[SearchQuery]) -> dict:
        return {"q": query.q, "limit": query.limit}

    @router.post("/items")
    async def _create(req: Body[CreateItem]) -> dict:
        return {"name": req.name, "price": req.price}

    @router.get("/boom")
    async def _boom(scope, proto) -> dict:
        raise RuntimeError("boom")

    @router.get("/custom")
    async def _custom(scope, proto) -> Response:
        return Response(b"<h1>hi</h1>", content_type="text/html")

    @router.post("/stream")
    async def _stream(scope, proto) -> None:
        transport = proto.response_stream(200, [("content-type", "text/event-stream")])
        await transport.send_str("data: 1\n\n")
        await transport.send_str("data: 2\n\n")

    return router


async def dispatch(
    router: Router, method: str, path: str, *, query: str = "", body: bytes = b""
) -> FakeProto:
    proto = FakeProto(body)
    scope = FakeScope(method=method, path=path, query_string=query)
    await router.dispatch(scope, proto)  # type: ignore[arg-type]
    return proto


@pytest.mark.parametrize(
    "method, path, query, body, expected_status, expected_body",
    [
        pytest.param("GET", "/health", "", b"", 200, {"status": "ok"}, id="static"),
        pytest.param(
            "GET", "/items/42", "", b"", 200, {"item_id": "42"}, id="path_param"
        ),
        pytest.param(
            "GET",
            "/search",
            "q=hello&limit=3",
            b"",
            200,
            {"q": "hello", "limit": 3},
            id="query_struct_coerced",
        ),
        pytest.param(
            "GET",
            "/search",
            "q=hello",
            b"",
            200,
            {"q": "hello", "limit": 10},
            id="query_default",
        ),
        pytest.param(
            "POST",
            "/items",
            "",
            b'{"name": "vase", "price": 9.5}',
            200,
            {"name": "vase", "price": 9.5},
            id="body_struct",
        ),
        pytest.param("GET", "/nope", "", b"", 404, {"error": "not found"}, id="404"),
        pytest.param(
            "DELETE",
            "/health",
            "",
            b"",
            405,
            {"error": "method not allowed"},
            id="405_static",
        ),
        pytest.param(
            "POST",
            "/items/42",
            "",
            b"",
            405,
            {"error": "method not allowed"},
            id="405_parametric",
        ),
        pytest.param(
            "GET",
            "/boom",
            "",
            b"",
            500,
            {"error": "internal server error"},
            id="handler_error_500",
        ),
    ],
)
async def test_dispatch(method, path, query, body, expected_status, expected_body):
    proto = await dispatch(make_router(), method, path, query=query, body=body)
    assert proto.status == expected_status
    assert json.loads(proto.body) == expected_body


@pytest.mark.parametrize(
    "query, body, path",
    [
        pytest.param("limit=3", b"", "/search", id="missing_required_query"),
        pytest.param("", b'{"name": "vase"}', "/items", id="missing_body_field"),
        pytest.param("", b'{"name": "v", "price": "x"}', "/items", id="bad_body_type"),
    ],
)
async def test_validation_errors_are_422(query, body, path):
    method = "POST" if body else "GET"
    proto = await dispatch(make_router(), method, path, query=query, body=body)
    assert proto.status == 422
    assert "error" in json.loads(proto.body)


async def test_custom_response_passthrough():
    proto = await dispatch(make_router(), "GET", "/custom")
    assert proto.status == 200
    assert proto.body == b"<h1>hi</h1>"
    assert ("content-type", "text/html") in proto.headers


async def test_streaming_handler_drives_protocol():
    proto = await dispatch(make_router(), "POST", "/stream")
    assert proto.stream is not None
    assert proto.stream.chunks == [b"data: 1\n\n", b"data: 2\n\n"]


async def test_include_with_prefix():
    api = make_router()
    root = Router()
    root.include(api, prefix="/v1")
    proto = await dispatch(root, "GET", "/v1/items/7")
    assert json.loads(proto.body) == {"item_id": "7"}


@pytest.mark.parametrize(
    "dep_kind",
    [
        pytest.param("sync", id="sync"),
        pytest.param("coro", id="coro"),
        pytest.param("gen", id="asyncgen_teardown"),
    ],
)
async def test_depends_injection(dep_kind):
    events: list[str] = []

    def sync_dep() -> str:
        return "sync-value"

    async def coro_dep() -> str:
        return "coro-value"

    async def gen_dep():
        events.append("setup")
        yield "gen-value"
        events.append("teardown")

    dep, expected = {
        "sync": (sync_dep, "sync-value"),
        "coro": (coro_dep, "coro-value"),
        "gen": (gen_dep, "gen-value"),
    }[dep_kind]

    router = Router()

    @router.get("/dep")
    async def _handler(value: Annotated[Any, Depends(dep)]) -> dict:
        return {"value": value}

    proto = await dispatch(router, "GET", "/dep")
    assert json.loads(proto.body) == {"value": expected}
    if dep_kind == "gen":
        assert events == ["setup", "teardown"]

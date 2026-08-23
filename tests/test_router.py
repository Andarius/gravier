import json
from typing import Annotated, Any

import msgspec
import pytest

from gravier import Body, Depends, Query, Response, Router
from gravier.testing import FakeProto, FakeScope


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10
    tags: list[str] = []


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
        return {"q": query.q, "limit": query.limit, "tags": query.tags}

    @router.get("/files/{name}.json")
    async def _file(scope, proto, name: str) -> dict:
        return {"name": name}

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
            {"q": "hello", "limit": 3, "tags": []},
            id="query_struct_coerced",
        ),
        pytest.param(
            "GET",
            "/search",
            "q=hello",
            b"",
            200,
            {"q": "hello", "limit": 10, "tags": []},
            id="query_default",
        ),
        pytest.param(
            "GET",
            "/search",
            "q=",
            b"",
            200,
            {"q": "", "limit": 10, "tags": []},
            id="query_blank_value_kept",
        ),
        pytest.param(
            "GET",
            "/search",
            "q=x&tags=a",
            b"",
            200,
            {"q": "x", "limit": 10, "tags": ["a"]},
            id="query_single_value_list_field",
        ),
        pytest.param(
            "GET",
            "/search",
            "q=x&tags=a&tags=b",
            b"",
            200,
            {"q": "x", "limit": 10, "tags": ["a", "b"]},
            id="query_repeated_list_field",
        ),
        pytest.param(
            "GET",
            "/files/data.json",
            "",
            b"",
            200,
            {"name": "data"},
            id="literal_dot_matches",
        ),
        pytest.param(
            "GET",
            "/files/dataXjson",
            "",
            b"",
            404,
            {"error": "not found"},
            id="literal_dot_not_wildcard",
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
    "method, query, body, path",
    [
        pytest.param("GET", "limit=3", b"", "/search", id="missing_required_query"),
        pytest.param(
            "POST", "", b'{"name": "vase"}', "/items", id="missing_body_field"
        ),
        pytest.param(
            "POST", "", b'{"name": "v", "price": "x"}', "/items", id="bad_body_type"
        ),
        pytest.param("POST", "", b"", "/items", id="empty_body"),
        pytest.param("POST", "", b'{"name": "v"', "/items", id="truncated_json"),
        pytest.param("POST", "", b"not json", "/items", id="malformed_json"),
    ],
)
async def test_validation_errors_are_422(method, query, body, path):
    proto = await dispatch(make_router(), method, path, query=query, body=body)
    assert proto.status == 422
    assert "error" in json.loads(proto.body)


@pytest.mark.parametrize(
    "method, path, expected_allow",
    [
        pytest.param("DELETE", "/health", "GET", id="static"),
        pytest.param("POST", "/items/42", "GET", id="parametric"),
    ],
)
async def test_405_includes_allow_header(method, path, expected_allow):
    proto = await dispatch(make_router(), method, path)
    assert proto.status == 405
    assert ("allow", expected_allow) in proto.headers


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

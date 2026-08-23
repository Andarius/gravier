import json

import msgspec

from gravier import Body, Query, Router, build_openapi


class SearchQuery(msgspec.Struct):
    q: str
    limit: int = 10


class CreateItem(msgspec.Struct):
    name: str


class ItemOut(msgspec.Struct):
    name: str
    id: int


def make_router() -> Router:
    router = Router()

    @router.get("/search")
    async def _search(query: Query[SearchQuery]) -> dict:
        """GET /search — Search things."""
        return {}

    @router.post("/items")
    async def _create(req: Body[CreateItem]) -> ItemOut:
        """Create an item."""
        return ItemOut(name="x", id=1)

    @router.get("/items/{item_id}")
    async def _get_item(scope, proto, item_id: str) -> ItemOut:
        return ItemOut(name="x", id=1)

    @router.get("/stream")
    async def _stream(scope, proto) -> None:
        return None

    @router.get("/openapi.json")
    async def _spec(scope, proto) -> dict:
        return {}

    return router


def test_spec_structure():
    spec = json.loads(build_openapi(make_router(), title="Test API", version="1.2.3"))
    assert spec["openapi"] == "3.1.0"
    assert spec["info"] == {"title": "Test API", "version": "1.2.3"}
    # excluded path
    assert "/openapi.json" not in spec["paths"]

    search = spec["paths"]["/search"]["get"]
    assert search["summary"] == "Search things."
    params = {p["name"]: p for p in search["parameters"]}
    assert params["q"]["required"] is True
    assert params["limit"]["required"] is False

    create = spec["paths"]["/items"]["post"]
    assert create["summary"] == "Create an item."
    assert (
        create["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/CreateItem"
    )
    assert create["responses"]["422"] == {"description": "Validation error"}
    schemas = set(spec["components"]["schemas"])
    assert schemas >= {"CreateItem", "ItemOut", "SearchQuery"}


def test_anonymous_return_types_are_inlined():
    spec = json.loads(build_openapi(make_router(), title="T"))
    # dict return: inline object schema, no dangling #/components/schemas/dict
    search_200 = spec["paths"]["/search"]["get"]["responses"]["200"]
    assert search_200["content"]["application/json"]["schema"] == {"type": "object"}
    assert "dict" not in spec["components"]["schemas"]


def test_query_only_route_documents_422():
    spec = json.loads(build_openapi(make_router(), title="T"))
    assert spec["paths"]["/search"]["get"]["responses"]["422"] == {
        "description": "Validation error"
    }


def test_path_params_are_emitted():
    spec = json.loads(build_openapi(make_router(), title="T"))
    params = spec["paths"]["/items/{item_id}"]["get"]["parameters"]
    assert {
        "name": "item_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    } in params


def test_none_return_has_no_body_schema():
    spec = json.loads(build_openapi(make_router(), title="T"))
    assert spec["paths"]["/stream"]["get"]["responses"]["200"] == {"description": "OK"}
    assert "NoneType" not in spec["components"]["schemas"]

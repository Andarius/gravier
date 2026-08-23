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

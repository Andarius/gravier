import msgspec
import niquests
import pytest

from gravier import Body, Router
from gravier.testing import ASGIBridge


class EchoBody(msgspec.Struct):
    message: str


@pytest.fixture
def client():
    router = Router()

    @router.get("/health")
    async def _health(scope, proto) -> dict:
        return {"status": "ok"}

    @router.post("/echo")
    async def _echo(req: Body[EchoBody]) -> dict:
        return {"echo": req.message}

    @router.post("/stream")
    async def _stream(scope, proto) -> None:
        transport = proto.response_stream(200, [("content-type", "text/event-stream")])
        await transport.send_str("data: a\n\n")
        await transport.send_str("data: b\n\n")

    with niquests.Session(app=ASGIBridge(router)) as session:
        yield session


def test_get_roundtrip(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_post_body_roundtrip(client):
    resp = client.post("/echo", json={"message": "hi"})
    assert resp.json() == {"echo": "hi"}


def test_stream_collected(client):
    resp = client.post("/stream")
    assert resp.status_code == 200
    assert resp.text == "data: a\n\ndata: b\n\n"


def test_unknown_route_404(client):
    resp = client.get("/nope")
    assert resp.status_code == 404

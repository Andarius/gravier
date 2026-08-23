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

    @router.get("/token")
    async def _token(scope, proto) -> dict:
        return {"token": scope.headers.get("x-token")}

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


def test_request_headers_propagated(client):
    resp = client.get("/token", headers={"x-token": "secret"})
    assert resp.json() == {"token": "secret"}


async def test_chunked_request_body_is_concatenated():
    router = Router()

    @router.post("/echo")
    async def _echo(req: Body[EchoBody]) -> dict:
        return {"echo": req.message}

    frames = [
        {"type": "http.request", "body": b'{"message"', "more_body": True},
        {"type": "http.request", "body": b': "split"}', "more_body": False},
    ]
    sent: list[dict] = []

    async def receive():
        return frames.pop(0)

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": "/echo", "query_string": b""}
    await ASGIBridge(router)(scope, receive, send)
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b'{"echo":"split"}'


async def test_lifespan_shutdown_acknowledged():
    frames = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
    sent: list[dict] = []

    async def receive():
        return frames.pop(0)

    async def send(msg):
        sent.append(msg)

    await ASGIBridge(Router())({"type": "lifespan"}, receive, send)
    assert sent == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]

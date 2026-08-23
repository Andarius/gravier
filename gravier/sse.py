from typing import Any

import msgspec

__all__ = ("SSE_HEADERS", "sse")

SSE_HEADERS = [
    ("content-type", "text/event-stream"),
    ("cache-control", "no-cache, no-transform"),
    ("x-accel-buffering", "no"),
    ("connection", "keep-alive"),
]
"""Response headers for an SSE stream, proxy-buffering disabled."""

_encoder = msgspec.json.Encoder()


def sse(event: str, data: Any) -> bytes:
    """Format one server-sent event; str passes through, the rest is JSON-encoded."""
    payload = data.encode() if isinstance(data, str) else _encoder.encode(data)
    return b"event: %b\ndata: %b\n\n" % (event.encode(), payload)

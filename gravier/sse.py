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
    if isinstance(data, str):
        # each line needs its own data: prefix per SSE framing
        payload = b"\n".join(b"data: " + line.encode() for line in data.split("\n"))
    else:
        payload = b"data: " + _encoder.encode(data)
    return b"event: %b\n%b\n\n" % (event.encode(), payload)

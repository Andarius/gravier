import pytest

from gravier import sse


@pytest.mark.parametrize(
    "event, data, expected",
    [
        pytest.param(
            "delta", {"text": "hi"}, b'event: delta\ndata: {"text":"hi"}\n\n', id="json"
        ),
        pytest.param(
            "done", "raw", b"event: done\ndata: raw\n\n", id="str_passthrough"
        ),
    ],
)
def test_sse_format(event, data, expected):
    assert sse(event, data) == expected

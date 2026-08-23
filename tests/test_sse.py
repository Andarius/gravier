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
        pytest.param(
            "x", "a\nb", b"event: x\ndata: a\ndata: b\n\n", id="multiline_str"
        ),
    ],
)
def test_sse_format(event, data, expected):
    assert sse(event, data) == expected

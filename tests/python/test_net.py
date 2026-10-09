import http.client
import urllib.error

import pytest

from scanner import net
from scanner.net import ApiError, http_get_json
from scanner.sources import travelpayouts as tp


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_from(outcomes):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    opener.calls = calls
    return opener


def http_error(code):
    return urllib.error.HTTPError("https://api.example/x", code, "error", {}, None)


def test_http_get_json_retries_then_succeeds():
    sleeps = []
    opener = opener_from([http_error(429), http_error(503), b'{"success": true, "data": []}'])
    body = http_get_json("https://api.example/x", {"a": "1"}, {}, opener=opener, sleep=sleeps.append)
    assert body == {"success": True, "data": []}
    assert sleeps == [2, 4]
    assert opener.calls[0] == "https://api.example/x?a=1"


def test_http_get_json_does_not_retry_auth_errors():
    sleeps = []
    with pytest.raises(ApiError, match="401"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([http_error(401)]), sleep=sleeps.append)
    assert sleeps == []


def test_http_get_json_gives_up_after_three_retries():
    sleeps = []
    with pytest.raises(ApiError, match="429"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([http_error(429)] * 4), sleep=sleeps.append)
    assert sleeps == [2, 4, 8]


def test_http_get_json_rejects_non_json():
    with pytest.raises(ApiError, match="invalid JSON"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([b"<html>"]), sleep=lambda s: None)


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),
        ConnectionResetError("reset by peer"),
        http.client.RemoteDisconnected("closed"),
        http.client.IncompleteRead(b""),
    ],
)
def test_http_get_json_retries_low_level_network_errors(error):
    sleeps = []
    opener = opener_from([error, b'{"success": true, "data": []}'])
    assert http_get_json("https://api.example/x", {}, {}, opener=opener, sleep=sleeps.append) == {"success": True, "data": []}
    assert sleeps == [2]


def test_http_get_json_turns_repeated_timeouts_into_api_error():
    with pytest.raises(ApiError, match="network error"):
        http_get_json("https://api.example/x", {}, {}, opener=opener_from([TimeoutError("t")] * 4), sleep=lambda s: None)


def test_travelpayouts_re_exports_the_shared_helper():
    assert tp.http_get_json is net.http_get_json
    assert tp.ApiError is net.ApiError


def test_error_messages_leave_out_the_query_string():
    with pytest.raises(ApiError) as info:
        http_get_json("https://api.example/x", {"api_key": "SECRET"}, {}, opener=opener_from([http_error(401)]), sleep=lambda s: None)
    assert "SECRET" not in str(info.value)

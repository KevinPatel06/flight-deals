"""JSON-over-HTTPS GET with retries, shared by every source.

Error messages name the URL without its query string, so API keys passed as parameters never reach logs.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BACKOFF = (2, 4, 8)
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    """A request failed for good (after retries, or with a non-retryable status)."""


def http_get_json(url: str, params: dict, headers: dict, *, opener=urllib.request.urlopen, sleep=time.sleep) -> dict:
    """GET url?params as JSON. Retries 429/5xx and network errors with 2s, 4s, 8s waits."""
    request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=headers)
    for attempt in range(len(BACKOFF) + 1):
        last = attempt == len(BACKOFF)
        try:
            with opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code not in RETRY_STATUS or last:
                raise ApiError(f"HTTP {err.code} for {url}") from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
            # Timeouts and dropped connections while reading surface as bare OSError/HTTPException.
            if last:
                raise ApiError(f"network error for {url}: {getattr(err, 'reason', err)!r}") from None
        except ValueError:
            raise ApiError(f"invalid JSON from {url}") from None
        sleep(BACKOFF[attempt])
    raise AssertionError("unreachable")

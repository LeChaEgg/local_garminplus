"""Intervals.icu API client.

Uses Basic Auth with username "API_KEY" and the user's personal API key as the
password (the auth scheme documented by intervals.icu for personal use). The
client only exposes the three endpoints we need:

  * GET /api/v1/athlete/{id}/wellness?oldest=&newest=
  * GET /api/v1/athlete/{id}/activities?oldest=&newest=
  * GET /api/v1/activity/{activity_id}?intervals=true

Athlete id may be "0" to refer to the authenticated athlete.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://intervals.icu"
DEFAULT_TIMEOUT_S = 30.0
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0
BACKOFF_MAX_S = 30.0
JITTER_FRAC = 0.25   # +/- 25% jitter on backoff


class IntervalsAPIError(RuntimeError):
    pass


class IntervalsClient:
    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_S,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise IntervalsAPIError("INTERVALS_API_KEY is not set")
        self.api_key = api_key
        self.athlete_id = athlete_id or "0"
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            auth=("API_KEY", api_key),
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> IntervalsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- endpoints ----

    def get_wellness(self, oldest: date, newest: date) -> list[dict[str, Any]]:
        url = f"/api/v1/athlete/{self.athlete_id}/wellness"
        params = {"oldest": oldest.isoformat(), "newest": newest.isoformat()}
        data = self._request("GET", url, params=params)
        return _as_list(data)

    def get_activities(self, oldest: date, newest: date) -> list[dict[str, Any]]:
        url = f"/api/v1/athlete/{self.athlete_id}/activities"
        params = {"oldest": oldest.isoformat(), "newest": newest.isoformat()}
        data = self._request("GET", url, params=params)
        return _as_list(data)

    def get_activity(self, activity_id: str, include_intervals: bool = True) -> dict[str, Any]:
        url = f"/api/v1/activity/{activity_id}"
        params: dict[str, Any] = {}
        if include_intervals:
            params["intervals"] = "true"
        data = self._request("GET", url, params=params)
        if not isinstance(data, dict):
            raise IntervalsAPIError(f"unexpected activity payload type: {type(data)}")
        return data

    # ---- transport ----

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        last_exc: Exception | None = None
        last_status: int | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as e:
                last_exc = e
                log.warning(
                    "Intervals.icu request error (attempt %d/%d): %s",
                    attempt + 1, MAX_RETRIES, e,
                )
                time.sleep(_sleep_for(attempt, retry_after=None))
                continue

            last_status = resp.status_code
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                log.warning(
                    "Intervals.icu %s %s -> %s (attempt %d/%d, retry in %.1fs)",
                    method, url, resp.status_code, attempt + 1, MAX_RETRIES,
                    retry_after if retry_after is not None else _sleep_for(attempt, None),
                )
                time.sleep(_sleep_for(attempt, retry_after))
                continue

            if resp.status_code in (401, 403):
                # Auth problems don't get retries — bail with a helpful message.
                raise IntervalsAPIError(
                    f"{method} {url} -> HTTP {resp.status_code}: authentication "
                    "failed. Check INTERVALS_API_KEY and INTERVALS_ATHLETE_ID in .env."
                )
            if resp.status_code >= 400:
                raise IntervalsAPIError(
                    f"{method} {url} -> HTTP {resp.status_code}: {resp.text[:200]}"
                )
            try:
                return resp.json()
            except ValueError as e:
                raise IntervalsAPIError(f"non-JSON response from {url}: {e}") from e

        last = (
            f"last HTTP {last_status}" if last_status else f"last error {last_exc}"
        )
        raise IntervalsAPIError(
            f"{method} {url} failed after {MAX_RETRIES} attempts ({last})"
        )


def _parse_retry_after(header_value: str | None) -> float | None:
    """Parse a Retry-After header. Accepts integer seconds; HTTP-date forms unsupported."""
    if not header_value:
        return None
    try:
        return max(0.0, float(header_value))
    except ValueError:
        return None


def _sleep_for(attempt: int, retry_after: float | None) -> float:
    """Compute backoff with jitter, honoring Retry-After if the server set it."""
    if retry_after is not None:
        return min(BACKOFF_MAX_S, retry_after)
    base = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** attempt))
    jitter = base * JITTER_FRAC
    return max(0.0, base + random.uniform(-jitter, jitter))


def _as_list(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Some endpoints wrap results.
        for key in ("data", "results", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
    raise IntervalsAPIError(f"expected JSON list, got {type(data).__name__}")

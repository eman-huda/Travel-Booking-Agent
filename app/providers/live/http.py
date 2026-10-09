"""HTTP client for live providers: timeouts, clear errors and an on-disk response cache.

The cache saves API quota (SerpApi's free tier is limited) and lets a researcher replay the
same live data across failure scenarios. Cached entries keep their original fetch time, so
the agent's freshness check still sees the true age of the data.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.tools.errors import ToolTimeoutError, UpstreamAPIError

# Wikimedia (Wikipedia, Wikivoyage) rejects requests with HTTP 403 unless the User-Agent names the app and
# gives contact details, in the form: <client name>/<version> (<contact information>) <library>/<version>.
# If you fork this project, put your own repository URL or email here.
USER_AGENT = (f"TravelAgentTestbed/1.0 (https://github.com/eman-huda/Travel-Booking-Agent; university research project) "
              f"python-httpx/{httpx.__version__}")
SECRET_PARAMS = {"api_key", "key", "token"}


class CachedHTTP:
    def __init__(self, cache_dir: Path, ttl_hours: float, timeout: float, client: httpx.Client | None = None):
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_hours * 3600
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        self.calls: list[dict] = []  # network calls made (no secrets), for diagnostics

    def _key(self, method: str, url: str, params: dict | None, data: dict | None) -> str:
        safe = {k: v for k, v in sorted((params or {}).items()) if k not in SECRET_PARAMS}
        raw = json.dumps([method, url, safe, data], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _read_cache(self, key: str) -> tuple[Any, str] | None:
        if self.ttl_seconds <= 0:
            return None
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        fetched = datetime.fromisoformat(entry["fetched_at"])
        if (datetime.now(timezone.utc) - fetched).total_seconds() > self.ttl_seconds:
            return None
        return entry["body"], entry["fetched_at"]

    def _write_cache(self, key: str, body: Any, fetched_at: str) -> None:
        if self.ttl_seconds <= 0:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps({"fetched_at": fetched_at, "body": body}), encoding="utf-8")
        except OSError:
            pass

    def request_json(self, method: str, url: str, *, service: str, params: dict | None = None,
                     data: dict | None = None, use_cache: bool = True) -> tuple[Any, str]:
        """Returns (parsed JSON body, ISO time the data was fetched)."""
        key = self._key(method, url, params, data)
        if use_cache:
            cached = self._read_cache(key)
            if cached is not None:
                return cached
        start = time.perf_counter()
        try:
            resp = self.client.request(method, url, params=params, data=data, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise ToolTimeoutError(service, self.timeout) from exc
        except httpx.HTTPError as exc:
            raise UpstreamAPIError(503, f"{service} could not be reached ({type(exc).__name__})") from exc
        finally:
            self.calls.append({"service": service, "url": url, "ms": round((time.perf_counter() - start) * 1000)})
        if resp.status_code >= 400:
            detail = ""
            try:
                body = resp.json()
                detail = body.get("error") or body.get("message") or "" if isinstance(body, dict) else ""
            except ValueError:
                pass
            raise UpstreamAPIError(resp.status_code, f"{service} returned an error{': ' + str(detail)[:160] if detail else ''}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise UpstreamAPIError(502, f"{service} returned a response that is not JSON") from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        if use_cache:
            self._write_cache(key, body, fetched_at)
        return body, fetched_at

    def get_json(self, url: str, *, service: str, params: dict | None = None, use_cache: bool = True):
        return self.request_json("GET", url, service=service, params=params, use_cache=use_cache)

    def post_form(self, url: str, *, service: str, data: dict, use_cache: bool = True):
        return self.request_json("POST", url, service=service, data=data, use_cache=use_cache)
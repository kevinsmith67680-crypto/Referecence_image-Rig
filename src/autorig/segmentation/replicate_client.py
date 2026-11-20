"""Minimal, defensive Replicate HTTP client.

* The API token comes from ``REPLICATE_API_TOKEN`` (or the constructor) and is
  never logged, printed, put in ``repr`` or included in exception messages
  (every message passes through :func:`ReplicateClient._scrub`).
* The token is sent ONLY to the API host. Output files on ``replicate.delivery``
  are downloaded without an Authorization header.
* Retries: GETs retry on network errors, 429 and 5xx. Creating a prediction is
  retried only when it is safe (connection never established, or HTTP 429),
  so a lost response can never cause a duplicate, double-billed prediction.
* Predictions are asynchronous: ``create_prediction`` returns immediately and
  ``wait`` polls until a terminal status (cancelling on timeout).

In the cloud development sandbox the token is injected by the network proxy;
set ``REPLICATE_API_TOKEN=proxy-injected`` there (any non-empty placeholder).
"""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import __version__

log = logging.getLogger("autorig.replicate")

API_BASE = "https://api.replicate.com/v1"
API_HOST = "api.replicate.com"
DOWNLOAD_HOSTS = ("replicate.delivery",)  # and its subdomains
TERMINAL = {"succeeded", "failed", "canceled"}


class ReplicateError(RuntimeError):
    """Base error. Messages never contain the API token."""


class ReplicateAuthError(ReplicateError):
    pass


class ReplicateHTTPError(ReplicateError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ReplicatePredictionError(ReplicateError):
    def __init__(self, message: str, prediction: dict[str, Any]):
        super().__init__(message)
        self.prediction = prediction


class ReplicateTimeout(ReplicateError):
    pass


class ReplicateClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = API_BASE,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        backoff_seconds: float = 1.0,
        timeout_seconds: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        token = token if token is not None else os.environ.get("REPLICATE_API_TOKEN", "")
        if not token.strip():
            raise ReplicateAuthError(
                "REPLICATE_API_TOKEN is not set. Export it (never commit it); in the cloud "
                "sandbox set it to the placeholder 'proxy-injected'.")
        self._token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._api = httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {self._token}", "User-Agent": f"autorig-studio/{__version__}"},
        )
        self._public = httpx.Client(transport=transport, timeout=timeout_seconds,
                                    headers={"User-Agent": f"autorig-studio/{__version__}"},
                                    follow_redirects=True)

    # -- hygiene ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"ReplicateClient(base_url={self.base_url!r}, token=<redacted>)"

    def _scrub(self, text: Any) -> str:
        s = str(text)
        return s.replace(self._token, "<redacted>") if self._token else s

    def close(self) -> None:
        self._api.close()
        self._public.close()

    def __enter__(self) -> "ReplicateClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- core request with retries ------------------------------------------------------
    def _request(self, method: str, url: str, *, idempotent: bool, **kwargs) -> httpx.Response:
        if not url.startswith("http"):
            url = f"{self.base_url}/{url.lstrip('/')}"
        if urlparse(url).hostname != urlparse(self.base_url).hostname:
            raise ReplicateError(f"refusing to send the API token to {urlparse(url).hostname}")
        attempt = 0
        while True:
            try:
                resp = self._api.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:  # nothing was sent: safe to retry
                err: Exception | None = exc
                resp = None
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if not idempotent:
                    raise ReplicateError(
                        f"{method} {url} failed after the request may have been sent "
                        f"({type(exc).__name__}); not retried to avoid a duplicate prediction") from None
                err, resp = exc, None
            else:
                err = None
            retryable = err is not None or (resp is not None and (
                resp.status_code == 429 or (idempotent and resp.status_code >= 500)))
            if not retryable:
                break
            if attempt >= self.max_retries:
                if err is not None:
                    raise ReplicateError(f"{method} {url}: {type(err).__name__} after {attempt + 1} attempts") from None
                break
            delay = self.backoff_seconds * (2 ** attempt)
            if resp is not None and resp.status_code == 429:
                try:
                    delay = max(delay, float(resp.headers.get("retry-after", delay)))
                except ValueError:
                    pass
            log.warning("Replicate %s %s: %s, retry %d/%d in %.1fs", method, urlparse(url).path,
                        type(err).__name__ if err else f"HTTP {resp.status_code}", attempt + 1, self.max_retries, delay)
            self._sleep(delay)
            attempt += 1
        if resp.status_code in (401, 403):
            raise ReplicateAuthError(f"Replicate rejected the credentials (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            detail = self._scrub(resp.text[:500])
            raise ReplicateHTTPError(f"{method} {urlparse(url).path}: HTTP {resp.status_code}: {detail}",
                                     status_code=resp.status_code)
        return resp

    def _json(self, method: str, url: str, *, idempotent: bool = True, **kwargs) -> dict[str, Any]:
        resp = self._request(method, url, idempotent=idempotent, **kwargs)
        try:
            return resp.json()
        except ValueError:
            raise ReplicateHTTPError(f"{method} {url}: response is not JSON", resp.status_code) from None

    # -- API ------------------------------------------------------------------------
    def get_model(self, owner: str, name: str) -> dict[str, Any]:
        return self._json("GET", f"models/{owner}/{name}")

    def list_versions(self, owner: str, name: str) -> list[dict[str, Any]]:
        out, url = [], f"models/{owner}/{name}/versions"
        while url:
            page = self._json("GET", url)
            out.extend(page.get("results", []))
            url = page.get("next")
        return out

    def get_version(self, owner: str, name: str, version_id: str) -> dict[str, Any]:
        return self._json("GET", f"models/{owner}/{name}/versions/{version_id}")

    def upload_file(self, path: str | Path) -> dict[str, Any]:
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            files = {"content": (path.name, fh, ctype)}
            # A lost response only orphans an unused file (no billing): safe to retry.
            return self._json("POST", "files", idempotent=True, files=files)

    def create_prediction(self, version: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return self._json("POST", "predictions", idempotent=False, json={"version": version, "input": inputs})

    def create_model_prediction(self, owner: str, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Official models (no public version list) run via the model endpoint."""
        return self._json("POST", f"models/{owner}/{name}/predictions", idempotent=False, json={"input": inputs})

    def get_prediction(self, prediction_id: str) -> dict[str, Any]:
        return self._json("GET", f"predictions/{prediction_id}")

    def cancel_prediction(self, prediction_id: str) -> dict[str, Any]:
        return self._json("POST", f"predictions/{prediction_id}/cancel", idempotent=True)

    def wait(self, prediction: dict[str, Any], *, poll_seconds: float = 1.0, timeout_seconds: float = 900.0,
             on_update: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        start = time.monotonic()
        pred = prediction
        while pred.get("status") not in TERMINAL:
            if time.monotonic() - start > timeout_seconds:
                try:
                    self.cancel_prediction(pred["id"])
                finally:
                    raise ReplicateTimeout(f"prediction {pred.get('id')} not finished after {timeout_seconds}s; cancelled")
            self._sleep(poll_seconds)
            pred = self.get_prediction(pred["id"])
            if on_update:
                on_update(pred)
        return pred

    def download(self, url: str, dest: str | Path) -> Path:
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in DOWNLOAD_HOSTS):
            raise ReplicateError(f"refusing to download from unexpected host {host!r}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        attempt = 0
        while True:
            try:
                with self._public.stream("GET", url) as resp:
                    if resp.status_code >= 500 or resp.status_code == 429:
                        raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                    if resp.status_code >= 400:
                        raise ReplicateHTTPError(f"download {urlparse(url).path}: HTTP {resp.status_code}",
                                                 resp.status_code)
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            fh.write(chunk)
                tmp.replace(dest)
                return dest
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                if attempt >= self.max_retries:
                    raise ReplicateError(f"download {urlparse(url).path} failed: {type(exc).__name__}") from None
                self._sleep(self.backoff_seconds * (2 ** attempt))
                attempt += 1

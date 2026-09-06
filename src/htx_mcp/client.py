"""Small, dependency-light async client for HTX's official REST APIs.

HTX uses the Huobi API v2 HMAC-SHA256 signing scheme for both spot and
derivatives private endpoints.  The client deliberately keeps endpoint paths
and request bodies visible to the server layer so the MCP tools map directly
to the official documentation instead of hiding exchange-specific behavior.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx


class HtxError(RuntimeError):
    """Base class for errors raised by the HTX client."""


class HtxConfigurationError(HtxError):
    """The server is missing required configuration."""


class HtxApiError(HtxError):
    """HTX returned an HTTP or application-level error."""

    def __init__(self, message: str, *, payload: Any = None, status_code: int | None = None):
        super().__init__(message)
        self.payload = payload
        self.status_code = status_code


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class HtxConfig:
    """Runtime configuration loaded from environment variables."""

    api_key: str | None = None
    api_secret: str | None = None
    base_url: str = "https://api.huobi.pro"
    futures_base_url: str = "https://api.hbdm.com"
    timeout_seconds: float = 20.0
    enable_trading: bool = False
    spot_account_id: str | None = None

    @classmethod
    def from_env(cls) -> "HtxConfig":
        timeout_raw = os.getenv("HTX_TIMEOUT_SECONDS", "20")
        try:
            timeout = max(1.0, float(timeout_raw))
        except ValueError as exc:
            raise HtxConfigurationError("HTX_TIMEOUT_SECONDS must be a number") from exc
        return cls(
            api_key=os.getenv("HTX_API_KEY") or None,
            api_secret=os.getenv("HTX_API_SECRET") or None,
            base_url=(os.getenv("HTX_API_BASE_URL") or "https://api.huobi.pro").rstrip("/"),
            futures_base_url=(os.getenv("HTX_FUTURES_API_BASE_URL") or "https://api.hbdm.com").rstrip("/"),
            timeout_seconds=timeout,
            enable_trading=_truthy(os.getenv("HTX_ENABLE_TRADING")),
            spot_account_id=os.getenv("HTX_SPOT_ACCOUNT_ID") or None,
        )


def utc_timestamp() -> str:
    """Return the UTC timestamp format required by HTX REST authentication."""

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _canonical_query(params: Mapping[str, Any]) -> str:
    """Create HTX's sorted RFC3986 query string.

    HTX documents ASCII-sorted key/value pairs and percent encoding with
    spaces represented as ``%20``.  ``doseq`` is intentionally not used here:
    private endpoint auth parameters are scalar values; endpoint lists belong
    in the JSON body for POST requests.
    """

    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        pairs.append((str(key), str(value)))
    pairs.sort(key=lambda item: (item[0], item[1]))
    return "&".join(f"{quote(key, safe='')}={quote(value, safe='')}" for key, value in pairs)


def sign_request(
    *,
    method: str,
    host: str,
    path: str,
    params: Mapping[str, Any],
    secret: str,
) -> str:
    """Return a base64 HMAC-SHA256 signature for a normalized HTX request."""

    normalized = _canonical_query(params)
    payload = "\n".join((method.upper(), host, path, normalized)).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class HtxClient:
    """Async HTTP client for public and signed HTX REST endpoints."""

    def __init__(self, config: HtxConfig | None = None, http: httpx.AsyncClient | None = None):
        self.config = config or HtxConfig.from_env()
        self._http = http or httpx.AsyncClient(timeout=self.config.timeout_seconds)
        self._owns_http = http is None

    @property
    def credentials_configured(self) -> bool:
        return bool(self.config.api_key and self.config.api_secret)

    def require_credentials(self) -> None:
        if not self.credentials_configured:
            raise HtxConfigurationError(
                "Private HTX tools require HTX_API_KEY and HTX_API_SECRET."
            )

    def _auth_query(
        self,
        method: str,
        path: str,
        query: Mapping[str, Any] | None,
        *,
        base_url: str,
    ) -> str:
        self.require_credentials()
        parts = urlsplit(base_url)
        host = parts.netloc
        params: dict[str, Any] = {
            "AccessKeyId": self.config.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": utc_timestamp(),
        }
        if query:
            params.update(query)
        params["Signature"] = sign_request(
            method=method,
            host=host,
            path=path,
            params=params,
            secret=self.config.api_secret or "",
        )
        return _canonical_query(params)

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | list[Any] | None = None,
        private: bool = False,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        """Send one official API request and return HTX's JSON envelope.

        For private GET requests endpoint parameters are signed in the query.
        For private POST requests endpoint parameters remain in the JSON body;
        only the authentication fields are placed in the signed query, as
        specified by HTX's authentication guide.
        """

        method = method.upper()
        request_base_url = (base_url or self.config.base_url).rstrip("/")
        parts = urlsplit(request_base_url)
        if not parts.scheme or not parts.netloc:
            raise HtxConfigurationError("HTX_API_BASE_URL must be an absolute URL")
        if not path.startswith("/"):
            path = f"/{path}"

        if private:
            auth_query = self._auth_query(
                method,
                path,
                query if method == "GET" else None,
                base_url=request_base_url,
            )
            url = f"{request_base_url}{path}?{auth_query}"
            request_kwargs: dict[str, Any] = {}
            if method != "GET":
                request_kwargs["json"] = body
        else:
            url = f"{request_base_url}{path}"
            request_kwargs = {}
            if method == "GET":
                request_kwargs["params"] = dict(query or {})
            else:
                request_kwargs["json"] = body

        try:
            response = await self._http.request(method, url, **request_kwargs)
        except httpx.HTTPError as exc:
            raise HtxApiError(f"HTX request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise HtxApiError(
                f"HTX returned non-JSON response (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 400:
            raise HtxApiError(
                f"HTX HTTP error {response.status_code}: {payload}",
                payload=payload,
                status_code=response.status_code,
            )

        status = str(payload.get("status", "")).lower() if isinstance(payload, dict) else ""
        code = payload.get("code") if isinstance(payload, dict) else None
        if status not in {"", "ok", "success"}:
            message = payload.get("err_msg") or payload.get("message") or payload.get("msg") or payload
            raise HtxApiError(f"HTX API error: {message}", payload=payload)
        if code not in {None, 200, "200", 0, "0"}:
            message = payload.get("err_msg") or payload.get("message") or payload.get("msg") or payload
            raise HtxApiError(f"HTX API error {code}: {message}", payload=payload)
        return payload

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()


def ensure_confirmation(
    client: HtxClient,
    *,
    tool_name: str,
    confirm: bool,
    request: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return a dry-run preview or ``None`` when a mutation may execute."""

    if not confirm:
        return {
            "executed": False,
            "dry_run": True,
            "reason": "confirm=true is required for a state-changing HTX tool",
            "tool": tool_name,
            "request": dict(request),
        }
    if not client.config.enable_trading:
        return {
            "executed": False,
            "dry_run": True,
            "reason": "HTX_ENABLE_TRADING is not enabled on the server",
            "tool": tool_name,
            "request": dict(request),
        }
    client.require_credentials()
    return None

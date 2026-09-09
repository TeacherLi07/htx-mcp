"""Persistent public HTX market WebSocket transport.

The wait tool consumes this module instead of polling REST endpoints.  The
transport intentionally owns only connection lifecycle and HTX wire details;
condition evaluation and indicator calculation remain in ``semantic_tools``.
"""

from __future__ import annotations

import asyncio
import gzip
import inspect
import json
import logging
import time
import uuid
import zlib
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from websockets.asyncio.client import connect as websocket_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .client import HtxConfig, HtxConfigurationError

logger = logging.getLogger(__name__)

Product = Literal["spot", "swap"]
ConnectFactory = Callable[[str], Any]


class HtxWebSocketError(RuntimeError):
    """Base error for the public HTX market stream."""


class HtxWebSocketSubscriptionError(HtxWebSocketError):
    """HTX rejected a subscription or returned a fatal stream error."""


class HtxWebSocketProtocolError(HtxWebSocketError):
    """The stream contained a malformed or undecodable message."""


@dataclass
class HtxStreamStats:
    """Bounded diagnostic counters for one product WebSocket stream."""

    connections: int = 0
    messages: int = 0
    disconnects: int = 0
    data_errors: int = 0
    warnings: list[Any] = field(default_factory=list)

    @property
    def reconnections(self) -> int:
        """Return successful connections after the first connection."""

        return max(0, self.connections - 1)

    def warn(
        self,
        kind: str,
        error: BaseException | str,
        *,
        retryable: bool,
    ) -> None:
        """Append a bounded, display-safe stream warning."""

        if len(self.warnings) >= 10:
            if len(self.warnings) == 10:
                self.warnings.append("Additional WebSocket warnings are omitted.")
            return
        message = str(error)
        self.warnings.append(
            {
                "kind": kind,
                "error_type": (
                    type(error).__name__ if not isinstance(error, str) else "Error"
                ),
                "message": message or "WebSocket operation failed",
                "retryable": retryable,
                "at_ms": int(time.time() * 1000),
            }
        )


def websocket_url(config: HtxConfig, product: Product) -> str:
    """Derive the public HTX stream URL from the configured REST host.

    CCXT keeps the WebSocket host selection alongside its exchange URL map.  A
    configurable REST host is similarly respected here, while the product
    path selects the spot or linear-swap public stream.
    """

    base_url = config.base_url if product == "spot" else config.futures_base_url
    parts = urlsplit(base_url)
    if not parts.scheme or not parts.netloc:
        raise HtxConfigurationError(
            "HTX WebSocket host requires an absolute configured API URL"
        )
    host = parts.netloc
    # CCXT's current HTX map uses api.hbdm.vn for the linear public stream;
    # retain the repository's api.hbdm.com REST default without inheriting its
    # older WebSocket hostname.
    if product == "swap" and host == "api.hbdm.com":
        host = "api.hbdm.vn"
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, "wss")
    path = "/ws" if product == "spot" else "/linear-swap-ws"
    return f"{scheme}://{host}{path}"


def _decode_message(raw: str | bytes | bytearray | memoryview) -> dict[str, Any]:
    """Decode HTX text or gzip-compressed binary JSON without float parsing."""

    if isinstance(raw, (bytes, bytearray, memoryview)):
        compressed = bytes(raw)
        try:
            raw = gzip.decompress(compressed)
        except (OSError, EOFError, zlib.error):
            # Some compatible gateways return an already decoded JSON frame.
            raw = compressed
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except UnicodeDecodeError as exc:
            raise HtxWebSocketProtocolError("HTX WebSocket frame is not UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise HtxWebSocketProtocolError("HTX WebSocket returned an unsupported frame")

    try:
        message = json.loads(text, parse_float=Decimal)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HtxWebSocketProtocolError("HTX WebSocket returned invalid JSON") from exc
    if not isinstance(message, dict):
        raise HtxWebSocketProtocolError("HTX WebSocket message must be an object")
    return message


def _heartbeat_response(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return the matching application-level response for HTX heartbeats."""

    if "ping" in message:
        return {"pong": message["ping"]}
    if message.get("action") == "ping":
        data = message.get("data")
        timestamp = data.get("ts") if isinstance(data, dict) else None
        return {"action": "pong", "data": {"ts": timestamp}}
    if message.get("op") == "ping":
        return {"op": "pong", "ts": message.get("ts")}
    return None


def _subscription_error(message: dict[str, Any]) -> str | None:
    """Extract a fatal HTX subscription error, if present."""

    status = str(message.get("status", "")).lower()
    if status == "error":
        return str(
            message.get("err-msg") or message.get("message") or "subscription rejected"
        )
    for key in ("err-code", "code"):
        code = message.get(key)
        if code is None or str(code) in {"", "0", "200"}:
            continue
        return str(
            message.get("err-msg") or message.get("message") or f"HTX error {code}"
        )
    return None


async def _close_quietly(websocket: Any) -> None:
    """Close a connection without masking the original stream outcome."""

    close = getattr(websocket, "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:  # pragma: no cover - depends on transport implementation
        logger.debug("Ignoring WebSocket close failure", exc_info=True)


class HtxMarketStream:
    """Subscribe to public HTX market channels with automatic reconnects."""

    def __init__(
        self,
        config: HtxConfig,
        *,
        connect_factory: ConnectFactory | None = None,
    ) -> None:
        self.config = config
        self._connect_factory = connect_factory

    def _connect(self, url: str) -> Any:
        if self._connect_factory is not None:
            return self._connect_factory(url)
        return websocket_connect(
            url,
            compression=None,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=2**20,
        )

    async def iter_messages(
        self,
        product: Product,
        channels: Iterable[str],
        *,
        deadline: float,
        stats: HtxStreamStats | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield subscribed market messages until the deadline or cancellation.

        ``websockets.connect`` is consumed as an asynchronous iterator.  Its
        reconnect loop uses exponential backoff for transient connection
        failures; every successful connection receives the full subscription
        set again, matching CCXT's connection/cache model.
        """

        stream_stats = stats or HtxStreamStats()
        subscription_channels = tuple(dict.fromkeys(channels))
        if not subscription_channels:
            return
        allowed_channels = {channel.lower() for channel in subscription_channels}
        connector = self._connect(websocket_url(self.config, product))
        iterator = connector.__aiter__()
        try:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                try:
                    websocket = await asyncio.wait_for(
                        iterator.__anext__(), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    return
                except StopAsyncIteration:
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stream_stats.warn("connection_error", exc, retryable=True)
                    raise HtxWebSocketError(
                        f"HTX {product} WebSocket connection failed"
                    ) from exc

                stream_stats.connections += 1
                try:
                    for channel in subscription_channels:
                        await websocket.send(
                            json.dumps(
                                {"sub": channel, "id": uuid.uuid4().hex},
                                separators=(",", ":"),
                            )
                        )
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return
                        try:
                            raw = await asyncio.wait_for(
                                websocket.recv(), timeout=remaining
                            )
                        except asyncio.TimeoutError:
                            return
                        except asyncio.CancelledError:
                            raise
                        except (ConnectionClosed, OSError, WebSocketException) as exc:
                            stream_stats.disconnects += 1
                            stream_stats.warn("connection_closed", exc, retryable=True)
                            break

                        try:
                            message = _decode_message(raw)
                        except HtxWebSocketProtocolError as exc:
                            stream_stats.data_errors += 1
                            stream_stats.warn("decode_error", exc, retryable=True)
                            break

                        response = _heartbeat_response(message)
                        if response is not None:
                            await websocket.send(
                                json.dumps(response, separators=(",", ":"))
                            )
                            continue

                        error = _subscription_error(message)
                        if error is not None:
                            raise HtxWebSocketSubscriptionError(error)

                        channel = message.get("ch")
                        if not isinstance(channel, str):
                            # Subscription acknowledgements and unrelated public
                            # notices don't satisfy a market condition.
                            continue
                        if channel.lower() not in allowed_channels:
                            continue
                        stream_stats.messages += 1
                        yield message
                except HtxWebSocketSubscriptionError:
                    raise
                except asyncio.CancelledError:
                    raise
                except (ConnectionClosed, OSError, WebSocketException) as exc:
                    stream_stats.disconnects += 1
                    stream_stats.warn("connection_closed", exc, retryable=True)
                finally:
                    await _close_quietly(websocket)
        finally:
            close_iterator = getattr(connector, "aclose", None)
            if close_iterator is not None:
                try:
                    result = close_iterator()
                    if inspect.isawaitable(result):
                        await result
                except Exception:  # pragma: no cover - connector-specific cleanup
                    logger.debug(
                        "Ignoring WebSocket iterator close failure", exc_info=True
                    )


__all__ = [
    "HtxMarketStream",
    "HtxStreamStats",
    "HtxWebSocketError",
    "HtxWebSocketProtocolError",
    "HtxWebSocketSubscriptionError",
    "websocket_url",
]

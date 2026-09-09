import asyncio
import gzip
import json
import time

from htx_mcp.client import HtxConfig
from htx_mcp.market_stream import HtxMarketStream, HtxStreamStats, websocket_url


class FakeWebSocket:
    def __init__(self, frames, *, fail_after=False):
        self.frames = list(frames)
        self.fail_after = fail_after
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        if self.fail_after:
            raise OSError("simulated disconnect")
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


class FakeConnector:
    def __init__(self, sockets):
        self.sockets = iter(sockets)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.sockets)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def test_websocket_url_follows_configured_hosts():
    config = HtxConfig(
        base_url="https://spot.example/api",
        futures_base_url="https://futures.example/api",
    )

    assert websocket_url(config, "spot") == "wss://spot.example/ws"
    assert websocket_url(config, "swap") == "wss://futures.example/linear-swap-ws"
    assert websocket_url(HtxConfig(), "swap") == "wss://api.hbdm.vn/linear-swap-ws"


def test_market_stream_reconnects_resubscribes_and_answers_heartbeat():
    first = FakeWebSocket(
        [
            gzip.compress(json.dumps({"ping": 123}).encode()),
            json.dumps({"action": "ping", "data": {"ts": 456}}),
            json.dumps({"op": "ping", "ts": 789}),
            json.dumps({"id": "ack", "status": "ok"}),
        ],
        fail_after=True,
    )
    second = FakeWebSocket(
        [
            json.dumps(
                {
                    "ch": "market.btcusdt.detail",
                    "ts": 1_700_000_000_001,
                    "tick": {"close": "60000.1"},
                }
            )
        ]
    )
    connector = FakeConnector([first, second])
    stream = HtxMarketStream(HtxConfig(), connect_factory=lambda _url: connector)
    stats = HtxStreamStats()

    async def collect_one():
        messages = []
        updates = stream.iter_messages(
            "spot",
            ["market.btcusdt.detail"],
            deadline=time.monotonic() + 1,
            stats=stats,
        )
        try:
            async for message in updates:
                messages.append(message)
                break
        finally:
            await updates.aclose()
        return messages

    messages = asyncio.run(collect_one())

    assert messages[0]["tick"]["close"] == "60000.1"
    assert stats.connections == 2
    assert stats.reconnections == 1
    assert stats.messages == 1
    assert stats.disconnects == 1
    assert first.sent[0]["sub"] == "market.btcusdt.detail"
    assert {"pong": 123} in first.sent
    assert {"action": "pong", "data": {"ts": 456}} in first.sent
    assert {"op": "pong", "ts": 789} in first.sent
    assert second.sent[0]["sub"] == "market.btcusdt.detail"
    assert first.sent[0]["id"] != second.sent[0]["id"]
    assert first.closed is True
    assert second.closed is True


def test_market_stream_honors_deadline_without_polling():
    websocket = FakeWebSocket([])
    connector = FakeConnector([websocket])
    stream = HtxMarketStream(HtxConfig(), connect_factory=lambda _url: connector)
    stats = HtxStreamStats()
    started = time.monotonic()

    async def collect_until_deadline():
        return [
            message
            async for message in stream.iter_messages(
                "spot",
                ["market.btcusdt.detail"],
                deadline=time.monotonic() + 0.05,
                stats=stats,
            )
        ]

    assert asyncio.run(collect_until_deadline()) == []
    assert time.monotonic() - started < 0.5
    assert stats.connections == 1
    assert stats.messages == 0
    assert websocket.closed is True

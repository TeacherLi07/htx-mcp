import asyncio
from dataclasses import replace
from urllib.parse import urlsplit

import htx_mcp.server as server
from htx_mcp.server import mcp


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class RoutingHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def request(self, method, url, **kwargs):
        path = urlsplit(url).path
        self.calls.append((method, path, kwargs))
        if path not in self.responses:
            raise AssertionError(f"unexpected HTTP path: {path}")
        return FakeResponse(self.responses[path])

    async def aclose(self):
        pass


def _install_router(monkeypatch, responses):
    http = RoutingHttp(responses)
    monkeypatch.setattr(server.client, "_http", http)
    monkeypatch.setattr(server.client, "_owns_http", False)
    monkeypatch.setattr(
        server.client,
        "config",
        replace(
            server.client.config,
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://spot.test",
            futures_base_url="https://futures.test",
            enable_trading=False,
            spot_account_id="1000",
        ),
    )
    return http


def _ok(data):
    return {"status": "ok", "code": 200, "data": data}


def test_swap_market_snapshot_matches_low_level_market_tools(monkeypatch):
    responses = {
        "/linear-swap-ex/market/detail/merged": {
            "status": "ok",
            "tick": {
                "close": "60000.1",
                "bid": ["60000.0", "2"],
                "ask": ["60000.2", "3"],
                "open": "59000",
                "high": "61000",
                "low": "58000",
                "vol": "123.45",
            },
            "ts": 1700000000000,
        },
        "/linear-swap-ex/market/depth": {
            "status": "ok",
            "tick": {
                "bids": [["60000.0", "2"], ["59999.9", "1"]],
                "asks": [["60000.2", "3"], ["60000.3", "4"]],
            },
            "ts": 1700000000001,
        },
        "/linear-swap-api/v1/swap_price_limit": _ok(
            [{"high_limit": "63000", "low_limit": "57000"}]
        ),
    }
    _install_router(monkeypatch, responses)

    low_ticker = asyncio.run(
        mcp.call_tool("futures_get_ticker", {"contract_code": "BTC-USDT"})
    )
    low_depth = asyncio.run(
        mcp.call_tool("futures_get_depth", {"contract_code": "BTC-USDT"})
    )
    low_limit = asyncio.run(
        mcp.call_tool("futures_get_price_limit", {"contract_code": "BTC-USDT"})
    )
    high = asyncio.run(
        mcp.call_tool(
            "htx_get_market_snapshot",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "profile": "execution",
            },
        )
    )

    assert high.is_error is False
    data = high.structured_content["data"]
    assert data["ticker"]["last"] == low_ticker.structured_content["tick"]["close"]
    assert data["ticker"]["bid"] == "60000.0"
    assert data["ticker"]["ask"] == "60000.2"
    assert data["depth"]["bids"] == low_depth.structured_content["tick"]["bids"][:20]
    assert data["depth"]["asks"] == low_depth.structured_content["tick"]["asks"][:20]
    assert data["price_limit"] == low_limit.structured_content["data"]


def test_instrument_rules_match_low_level_contract_info(monkeypatch):
    contract = {
        "symbol": "BTC",
        "contract_code": "BTC-USDT",
        "contract_size": "0.001",
        "price_tick": "0.1",
        "volume_tick": "1",
        "contract_status": 1,
    }
    _install_router(
        monkeypatch,
        {"/linear-swap-api/v1/swap_contract_info": _ok([contract])},
    )

    low = asyncio.run(
        mcp.call_tool("futures_get_contracts", {"contract_code": "BTC-USDT"})
    )
    high = asyncio.run(
        mcp.call_tool(
            "htx_get_instrument_rules",
            {"product": "swap", "instrument": "BTC-USDT"},
        )
    )

    assert high.structured_content["matched"] == low.structured_content["data"][0]


def test_account_snapshot_matches_low_level_account_tools(monkeypatch):
    balances = [{"margin_account": "BTC-USDT", "margin_balance": "1000"}]
    positions = [{"contract_code": "BTC-USDT", "volume": "2", "direction": "buy"}]
    _install_router(
        monkeypatch,
        {
            "/linear-swap-api/v1/swap_account_info": _ok(balances),
            "/linear-swap-api/v1/swap_position_info": _ok(positions),
        },
    )

    low_account = asyncio.run(
        mcp.call_tool(
            "futures_get_account_info",
            {"margin_mode": "isolated", "contract_code": "BTC-USDT"},
        )
    )
    low_positions = asyncio.run(
        mcp.call_tool(
            "futures_get_positions",
            {"margin_mode": "isolated", "contract_code": "BTC-USDT"},
        )
    )
    high = asyncio.run(
        mcp.call_tool(
            "htx_get_account_snapshot",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "include": ["balances", "positions"],
            },
        )
    )

    assert high.structured_content["balances"] == low_account.structured_content["data"]
    assert (
        high.structured_content["positions"] == low_positions.structured_content["data"]
    )


def test_trade_preview_request_matches_low_level_order_request(monkeypatch):
    responses = {
        "/linear-swap-api/v1/swap_contract_info": _ok(
            [
                {
                    "contract_code": "BTC-USDT",
                    "volume_tick": "1",
                    "price_tick": "0.1",
                    "min_volume": "1",
                }
            ]
        ),
        "/linear-swap-ex/market/detail/merged": {
            "status": "ok",
            "tick": {
                "close": "60000.1",
                "bid": ["60000.0", "1"],
                "ask": ["60000.2", "1"],
            },
        },
        "/linear-swap-ex/market/depth": {
            "status": "ok",
            "tick": {"bids": [], "asks": []},
        },
        "/linear-swap-api/v1/swap_price_limit": _ok(
            [{"high_limit": "63000", "low_limit": "57000"}]
        ),
    }
    _install_router(monkeypatch, responses)
    intent = {
        "product": "swap",
        "instrument": "BTC-USDT",
        "action": "open",
        "side": "buy",
        "order_kind": "limit",
        "quantity": "1",
        "price": "60000.1",
    }

    preview = asyncio.run(mcp.call_tool("htx_preview_trade", {"intent": intent}))
    low = asyncio.run(
        mcp.call_tool(
            "futures_place_order",
            {
                "contract_code": "BTC-USDT",
                "volume": "1",
                "direction": "buy",
                "order_price_type": "limit",
                "price": "60000.1",
                "offset": "open",
            },
        )
    )

    assert preview.structured_content["status"] == "ready"
    assert (
        preview.structured_content["request"]
        == low.structured_content["request"]["body"]
    )

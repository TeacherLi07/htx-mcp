import asyncio
import time
from dataclasses import replace
from urllib.parse import urlsplit

import pytest

from htx_mcp import server
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


def test_technical_indicators_are_decimal_calculated_from_completed_candles(
    monkeypatch,
):
    candles = [
        {
            "id": 1_700_000_000 + index * 3_600,
            "open": str(index + 1),
            "high": str(index + 2),
            "low": str(index),
            "close": str(index + 1),
            "vol": str((index + 1) * 10),
        }
        for index in range(6)
    ]
    _install_router(
        monkeypatch,
        {"/market/history/kline": {"status": "ok", "data": list(reversed(candles))}},
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_get_technical_indicators",
            {
                "product": "spot",
                "instrument": "btcusdt",
                "period": "60min",
                "candle_size": 6,
                "indicators": [
                    "ma:3",
                    "ema:3",
                    "rsi:3",
                    "atr:3",
                    "volume_sma:3",
                    "bbands:3,2",
                    "macd:2,3,2",
                    "kdj:3,1,1",
                ],
            },
        )
    )

    assert result.is_error is False
    data = result.structured_content
    assert data["completed_candles"] == 6
    assert data["omitted_incomplete_candles"] == 0
    assert data["indicators"]["sma:3"]["value"] == "5"
    assert data["indicators"]["ema:3"]["value"] == "5"
    assert data["indicators"]["rsi:3"]["value"] == "100"
    assert data["indicators"]["atr:3"]["value"] == "2"
    assert data["indicators"]["volume_sma:3"]["value"] == "50"
    assert data["indicators"]["bbands:3,2"]["middle"] == "5"
    assert set(data["indicators"]["macd:2,3,2"]) == {
        "macd",
        "signal",
        "histogram",
    }
    assert set(data["indicators"]["kdj:3,1,1"]) == {"k", "d", "j"}


def test_technical_indicators_report_insufficient_data_without_failing(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/linear-swap-ex/market/history/kline": {
                "status": "ok",
                "data": [
                    {
                        "id": 1_700_000_000,
                        "open": "1",
                        "high": "2",
                        "low": "0",
                        "close": "1",
                        "vol": "10",
                    }
                ],
            }
        },
    )

    data = asyncio.run(
        mcp.call_tool(
            "htx_get_technical_indicators",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "indicators": ["rsi:14"],
            },
        )
    ).structured_content

    assert data["indicators"]["rsi:14"] == {
        "status": "insufficient_data",
        "required_candles": 15,
        "available_candles": 1,
    }


def test_market_wait_triggers_on_a_declarative_price_condition(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/linear-swap-ex/market/detail/merged": {
                "status": "ok",
                "tick": {"close": "60000.1"},
            }
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_wait_for_market_event",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "conditions": [
                    {
                        "metric": "last_price",
                        "operator": "gte",
                        "value": "60000",
                    }
                ],
                "timeout_seconds": 30,
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content["status"] == "triggered"
    assert result.structured_content["polls"] == 1
    assert result.structured_content["matched_conditions"] == [0]
    assert result.structured_content["observations"] == {"last_price": "60000.1"}


def test_market_wait_supports_indicator_aliases_and_all_matching(monkeypatch):
    candles = [
        {
            "id": 1_700_000_000 + index * 3_600,
            "open": str(index + 1),
            "high": str(index + 2),
            "low": str(index),
            "close": str(index + 1),
            "vol": "10",
        }
        for index in range(6)
    ]
    _install_router(
        monkeypatch,
        {
            "/market/detail/merged": {"status": "ok", "tick": {"close": "60000"}},
            "/market/history/kline": {"status": "ok", "data": list(reversed(candles))},
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_wait_for_market_event",
            {
                "product": "spot",
                "instrument": "btcusdt",
                "match": "all",
                "conditions": [
                    {
                        "metric": "last_price",
                        "operator": "gte",
                        "value": "60000",
                    },
                    {
                        "metric": "indicator",
                        "indicator": "ma:3",
                        "operator": "gte",
                        "value": "5",
                    },
                ],
            },
        )
    )

    assert result.structured_content["status"] == "triggered"
    assert result.structured_content["matched_conditions"] == [0, 1]
    assert result.structured_content["observations"] == {
        "last_price": "60000",
        "sma:3/value": "5",
    }


def test_market_wait_enforces_its_deadline_while_a_market_call_is_slow(monkeypatch):
    async def slow_ticker(_symbol):
        await asyncio.sleep(2)
        return {"status": "ok", "tick": {"close": "60000"}}

    monkeypatch.setattr(server, "spot_get_ticker", slow_ticker)
    started = time.perf_counter()
    result = asyncio.run(
        mcp.call_tool(
            "htx_wait_for_market_event",
            {
                "product": "spot",
                "instrument": "btcusdt",
                "conditions": [
                    {
                        "metric": "last_price",
                        "operator": "gte",
                        "value": "70000",
                    }
                ],
                "timeout_seconds": 1,
            },
        )
    )
    elapsed = time.perf_counter() - started

    assert result.structured_content["status"] == "data_unavailable"
    assert result.structured_content["polls"] == 1
    assert elapsed < 1.5


def test_market_wait_tool_warns_that_it_hides_intermediate_market_updates():
    tools = asyncio.run(mcp.list_tools())
    wait_tool = next(tool for tool in tools if tool.name == "htx_wait_for_market_event")

    assert "no intermediate\nmarket updates" in wait_tool.description
    assert "then re-check market snapshots after every return" in wait_tool.description


def test_market_wait_returns_timeout_without_a_matching_condition(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/market/detail/merged": {
                "status": "ok",
                "tick": {"close": "60000.1"},
            }
        },
    )
    result = asyncio.run(
        mcp.call_tool(
            "htx_wait_for_market_event",
            {
                "product": "spot",
                "instrument": "btcusdt",
                "conditions": [
                    {
                        "metric": "last_price",
                        "operator": "gte",
                        "value": "70000",
                    }
                ],
                "timeout_seconds": 1,
                "poll_interval_seconds": 1,
            },
        )
    )

    assert result.structured_content["status"] == "timed_out"
    assert result.structured_content["polls"] == 2
    assert result.structured_content["matched_conditions"] == []


def test_swap_market_snapshot_includes_contract_rules_when_requested(monkeypatch):
    contract = {
        "contract_code": "BTC-USDT",
        "volume_tick": "1",
        "price_tick": "0.1",
    }
    _install_router(
        monkeypatch,
        {"/linear-swap-api/v1/swap_contract_info": _ok([contract])},
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_get_market_snapshot",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "include": ["contracts"],
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content["data"]["contracts"] == [contract]
    assert result.structured_content["warnings"] == []


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


def test_account_snapshot_reports_product_dependent_fields(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/v1/account/accounts/1000/balance": _ok({"list": []}),
            "/v1/order/openOrders": _ok([]),
        },
    )

    spot = asyncio.run(
        mcp.call_tool(
            "htx_get_account_snapshot",
            {
                "product": "spot",
                "instrument": "btcusdt",
                "include": ["balances", "positions", "api_status"],
            },
        )
    ).structured_content

    assert spot["warnings"] == [
        "api_status: field is only supported for swap account snapshots",
        "positions: field is only supported for swap account snapshots",
    ]

    _install_router(
        monkeypatch,
        {
            "/linear-swap-api/v1/swap_account_info": _ok([]),
            "/linear-swap-api/v1/swap_position_info": _ok([]),
            "/linear-swap-api/v1/swap_api_trading_status": _ok({}),
        },
    )
    swap = asyncio.run(
        mcp.call_tool("htx_get_account_snapshot", {"product": "swap"})
    ).structured_content

    assert swap["warnings"] == [
        "open_orders: instrument is required for swap account snapshots"
    ]


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

    invalid_quantity = asyncio.run(
        mcp.call_tool(
            "htx_validate_trade_intent",
            {"intent": {**intent, "quantity": "1.5"}},
        )
    )
    assert invalid_quantity.structured_content["status"] == "blocked"
    assert "request" not in invalid_quantity.structured_content
    assert "market" not in invalid_quantity.structured_content
    assert any(
        check["code"] == "quantity_precision"
        for check in invalid_quantity.structured_content["checks"]
    )

    market_preview = asyncio.run(
        mcp.call_tool(
            "htx_preview_trade",
            {
                "intent": {
                    **intent,
                    "order_kind": "market",
                    "price": "60000.15",
                }
            },
        )
    )
    assert market_preview.structured_content["status"] == "ready"
    assert "price" not in market_preview.structured_content["request"]
    assert any(
        check["code"] == "market_price_ignored"
        for check in market_preview.structured_content["checks"]
    )


def test_blocked_submission_uses_the_stable_submission_envelope(monkeypatch):
    _install_router(
        monkeypatch,
        {
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
                "tick": {"close": "60000.1"},
            },
            "/linear-swap-ex/market/depth": {
                "status": "ok",
                "tick": {"bids": [], "asks": []},
            },
            "/linear-swap-api/v1/swap_price_limit": _ok([]),
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_submit_trade",
            {
                "intent": {
                    "product": "swap",
                    "instrument": "BTC-USDT",
                    "side": "buy",
                    "order_kind": "limit",
                    "quantity": "1.5",
                    "price": "60000.1",
                }
            },
        )
    ).structured_content

    assert set(result) == {"validation", "execution"}
    assert result["validation"]["status"] == "blocked"
    assert result["execution"] == {
        "executed": False,
        "dry_run": True,
        "reason": "validation_blocked",
    }


@pytest.mark.parametrize("order_kind", ["ioc", "fok"])
def test_close_position_passes_price_for_non_market_close_modes(
    monkeypatch, order_kind
):
    _install_router(
        monkeypatch,
        {
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
            "/linear-swap-api/v1/swap_price_limit": _ok([]),
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_close_position",
            {
                "instrument": "BTC-USDT",
                "quantity": "1",
                "side": "sell",
                "order_kind": order_kind,
                "price": "60000.1",
            },
        )
    )

    validation = result.structured_content["validation"]
    assert validation["status"] == "ready"
    assert validation["request"]["price"] == "60000.1"
    assert not any(check["code"] == "missing_price" for check in validation["checks"])


def test_swap_reconciliation_uses_client_order_id(monkeypatch):
    http = _install_router(
        monkeypatch,
        {
            "/linear-swap-api/v1/swap_order_info": _ok(
                [{"order_id": "987", "client_order_id": 123456}]
            )
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_reconcile_trade",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "client_order_id": 123456,
            },
        )
    )

    assert result.is_error is False
    assert result.structured_content["order"] == [
        {"order_id": "987", "client_order_id": 123456}
    ]
    assert http.calls[-1][2]["json"] == {
        "contract_code": "BTC-USDT",
        "client_order_id": "123456",
    }


def test_spot_market_order_preview_omits_supplied_price(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/v1/common/symbols": _ok([{"symbol": "btcusdt", "amount-precision": 8}]),
            "/market/detail/merged": {
                "status": "ok",
                "tick": {"close": "60000.1"},
            },
            "/market/depth": {
                "status": "ok",
                "tick": {"bids": [], "asks": []},
            },
        },
    )

    result = asyncio.run(
        mcp.call_tool(
            "htx_preview_trade",
            {
                "intent": {
                    "product": "spot",
                    "instrument": "btcusdt",
                    "side": "buy",
                    "order_kind": "market",
                    "quantity": "0.01",
                    "price": "60000.1",
                }
            },
        )
    )

    assert result.structured_content["status"] == "ready"
    assert "price" not in result.structured_content["request"]


def test_spot_validation_uses_product_specific_amount_minimums(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/v1/common/symbols": _ok(
                [
                    {
                        "symbol": "btcusdt",
                        "amount-precision": 8,
                        "value-precision": 2,
                        "limit-order-min-order-amt": "0.001",
                        "sell-market-min-order-amt": "0.002",
                        "min-order-value": "10",
                        "price-tick": "0.1",
                    }
                ]
            ),
            "/market/detail/merged": {
                "status": "ok",
                "tick": {"close": "60000.1"},
            },
            "/market/depth": {
                "status": "ok",
                "tick": {"bids": [], "asks": []},
            },
        },
    )

    buy_market = asyncio.run(
        mcp.call_tool(
            "htx_validate_trade_intent",
            {
                "intent": {
                    "product": "spot",
                    "instrument": "btcusdt",
                    "side": "buy",
                    "order_kind": "market",
                    "quantity": "1.234",
                }
            },
        )
    ).structured_content
    sell_market = asyncio.run(
        mcp.call_tool(
            "htx_validate_trade_intent",
            {
                "intent": {
                    "product": "spot",
                    "instrument": "btcusdt",
                    "side": "sell",
                    "order_kind": "market",
                    "quantity": "0.001",
                }
            },
        )
    ).structured_content
    limit_order = asyncio.run(
        mcp.call_tool(
            "htx_validate_trade_intent",
            {
                "intent": {
                    "product": "spot",
                    "instrument": "btcusdt",
                    "side": "buy",
                    "order_kind": "limit",
                    "quantity": "0.001",
                    "price": "100",
                }
            },
        )
    ).structured_content

    assert {check["code"] for check in buy_market["checks"]} >= {
        "quantity_precision",
        "order_value_below_minimum",
    }
    assert "quantity_below_minimum" in {
        check["code"] for check in sell_market["checks"]
    }
    assert "order_value_below_minimum" in {
        check["code"] for check in limit_order["checks"]
    }


def test_spot_submit_dry_run_does_not_resolve_an_account(monkeypatch):
    _install_router(
        monkeypatch,
        {
            "/v1/common/symbols": _ok([{"symbol": "btcusdt", "amount-precision": 8}]),
            "/market/detail/merged": {
                "status": "ok",
                "tick": {"close": "60000.1"},
            },
            "/market/depth": {
                "status": "ok",
                "tick": {"bids": [], "asks": []},
            },
        },
    )
    monkeypatch.setattr(
        server.client,
        "config",
        replace(
            server.client.config,
            api_key=None,
            api_secret=None,
            spot_account_id=None,
        ),
    )

    async def fail_account_resolution(_account_id):
        raise AssertionError("dry-run must not resolve a spot account")

    monkeypatch.setattr(server, "_resolve_spot_account_id", fail_account_resolution)

    result = asyncio.run(
        mcp.call_tool(
            "htx_submit_trade",
            {
                "intent": {
                    "product": "spot",
                    "instrument": "btcusdt",
                    "side": "buy",
                    "order_kind": "market",
                    "quantity": "0.01",
                }
            },
        )
    )

    assert result.structured_content["validation"]["status"] == "ready"
    assert result.structured_content["execution"]["dry_run"] is True
    assert (
        result.structured_content["validation"]["request"]["account-id"]
        == "<auto-resolve>"
    )

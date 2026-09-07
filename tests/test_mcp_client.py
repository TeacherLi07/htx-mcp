import asyncio
from dataclasses import replace

import anyio
import pytest
from mcp import ClientSession

from htx_mcp import server


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse({"status": "ok", "data": {"accepted": True}})

    async def aclose(self):
        pass


TOOL_ARGUMENTS = {
    "spot_get_ticker": {"symbol": "btcusdt"},
    "spot_get_tickers": {},
    "spot_get_klines": {"symbol": "btcusdt"},
    "spot_get_depth": {"symbol": "btcusdt"},
    "spot_get_recent_trades": {"symbol": "btcusdt"},
    "spot_get_symbols": {},
    "spot_get_currencies": {},
    "spot_get_currency_reference": {"currency": "usdt"},
    "spot_get_market_status": {},
    "spot_get_server_timestamp": {},
    "spot_get_accounts": {},
    "htx_diagnose_private_access": {},
    "spot_get_account_balance": {"account_id": "1000"},
    "spot_get_open_orders": {"account_id": "1000", "symbol": "btcusdt"},
    "spot_get_order": {"order_id": "123"},
    "spot_get_order_by_client_id": {"client_order_id": "client-123"},
    "spot_get_match_results": {"symbol": "btcusdt"},
    "spot_get_order_match_results": {"order_id": "123"},
    "spot_get_history_orders": {"symbol": "btcusdt"},
    "spot_get_recent_history_orders": {},
    "spot_dead_man_switch": {"timeout_seconds": 0},
    "spot_place_order": {
        "symbol": "btcusdt",
        "order_type": "buy-limit",
        "amount": 1,
        "price": 100,
    },
    "spot_cancel_order": {"order_id": "123"},
    "spot_cancel_by_client_id": {"client_order_id": "client-123"},
    "spot_cancel_orders_by_ids": {"order_ids": ["123"]},
    "spot_cancel_open_orders": {"account_id": "1000"},
    "futures_get_contracts": {},
    "futures_get_ticker": {"contract_code": "BTC-USDT"},
    "futures_get_tickers": {},
    "futures_get_depth": {"contract_code": "BTC-USDT"},
    "futures_get_klines": {"contract_code": "BTC-USDT"},
    "futures_get_recent_trades": {"contract_code": "BTC-USDT"},
    "futures_get_index": {"contract_code": "BTC-USDT"},
    "futures_get_price_limit": {"contract_code": "BTC-USDT"},
    "futures_get_open_interest": {},
    "futures_get_funding_rate": {"contract_code": "BTC-USDT"},
    "futures_get_batch_funding_rate": {"contract_code": "BTC-USDT"},
    "futures_get_historical_funding_rate": {"contract_code": "BTC-USDT"},
    "futures_get_api_state": {},
    "futures_get_risk_info": {},
    "futures_get_insurance_fund": {"contract_code": "BTC-USDT"},
    "futures_get_liquidation_orders": {"contract_code": "BTC-USDT"},
    "futures_get_elite_ratios": {"contract_code": "BTC-USDT"},
    "futures_get_account_info": {"contract_code": "BTC-USDT"},
    "futures_get_positions": {"contract_code": "BTC-USDT"},
    "futures_get_account_position_info": {"contract_code": "BTC-USDT"},
    "futures_get_available_leverage": {"contract_code": "BTC-USDT"},
    "futures_get_open_orders": {"contract_code": "BTC-USDT"},
    "futures_get_order_info": {"contract_code": "BTC-USDT", "order_id": "123"},
    "futures_get_order_detail": {"contract_code": "BTC-USDT", "order_id": "123"},
    "futures_get_history_orders": {"contract_code": "BTC-USDT"},
    "futures_get_match_results": {"contract_code": "BTC-USDT"},
    "futures_get_financial_records": {"contract_code": "BTC-USDT"},
    "futures_place_order": {
        "contract_code": "BTC-USDT",
        "volume": 1,
        "direction": "buy",
        "price": 100,
    },
    "futures_place_batch_orders": {
        "orders": [
            {
                "contract_code": "BTC-USDT",
                "volume": 1,
                "direction": "buy",
                "order_price_type": "limit",
                "price": 100,
            }
        ]
    },
    "futures_cancel_order": {"contract_code": "BTC-USDT", "order_id": "123"},
    "futures_cancel_all_orders": {"contract_code": "BTC-USDT"},
    "futures_switch_leverage": {"contract_code": "BTC-USDT", "lever_rate": 5},
    "futures_lightning_close_position": {
        "contract_code": "BTC-USDT",
        "volume": 1,
        "direction": "sell",
    },
    "futures_place_trigger_order": {
        "contract_code": "BTC-USDT",
        "trigger_type": "ge",
        "trigger_price": 100,
        "volume": 1,
        "direction": "buy",
        "order_price": 101,
    },
    "futures_get_trigger_open_orders": {"contract_code": "BTC-USDT"},
    "futures_get_trigger_history": {"contract_code": "BTC-USDT"},
    "futures_cancel_trigger_order": {"contract_code": "BTC-USDT", "order_id": "123"},
    "futures_cancel_all_trigger_orders": {},
    "futures_switch_position_mode": {
        "contract_code": "BTC-USDT",
        "position_mode": "single_side",
    },
    "futures_get_balance_valuation": {},
    "futures_get_api_trading_status": {},
}


MUTATION_TOOLS = {
    "spot_dead_man_switch",
    "spot_place_order",
    "spot_cancel_order",
    "spot_cancel_by_client_id",
    "spot_cancel_orders_by_ids",
    "spot_cancel_open_orders",
    "futures_place_order",
    "futures_place_batch_orders",
    "futures_cancel_order",
    "futures_cancel_all_orders",
    "futures_switch_leverage",
    "futures_lightning_close_position",
    "futures_place_trigger_order",
    "futures_cancel_trigger_order",
    "futures_cancel_all_trigger_orders",
    "futures_switch_position_mode",
}


async def _call_tool(tool_name, arguments):
    client_to_server_send, client_to_server_receive = anyio.create_memory_object_stream(
        100
    )
    server_to_client_send, server_to_client_receive = anyio.create_memory_object_stream(
        100
    )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(
            server.mcp._lowlevel_server.run,
            client_to_server_receive,
            server_to_client_send,
            server.mcp._lowlevel_server.create_initialization_options(),
        )
        async with ClientSession(
            server_to_client_receive, client_to_server_send
        ) as mcp_client:
            initialize_result = await mcp_client.initialize()
            assert initialize_result.server_info.name == "htx-official-api"

            listed_tools = await mcp_client.list_tools()
            listed_names = {tool.name for tool in listed_tools.tools}
            assert set(TOOL_ARGUMENTS) <= listed_names

            result = await mcp_client.call_tool(tool_name, arguments)
            assert result.is_error is False, (
                f"{tool_name} returned an MCP error: {result}"
            )
            assert result.structured_content is not None, tool_name
            if tool_name in MUTATION_TOOLS:
                assert result.structured_content["dry_run"] is True, tool_name
                assert result.structured_content["executed"] is False, tool_name

        task_group.cancel_scope.cancel()


@pytest.mark.parametrize("tool_name", sorted(TOOL_ARGUMENTS))
def test_mcp_client_can_call_every_published_tool(monkeypatch, tool_name):
    fake_http = FakeHttp()
    monkeypatch.setattr(server.client, "_http", fake_http)
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

    asyncio.run(_call_tool(tool_name, TOOL_ARGUMENTS[tool_name]))

    if tool_name in MUTATION_TOOLS:
        assert not fake_http.calls, f"{tool_name} must remain a dry-run"
    else:
        assert fake_http.calls, f"{tool_name} should reach the mocked HTX client"
    assert all("test-secret" not in str(call) for call in fake_http.calls)

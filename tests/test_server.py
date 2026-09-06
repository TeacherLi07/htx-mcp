import asyncio
from dataclasses import replace

import htx_mcp.server as server
from htx_mcp.server import mcp


def test_server_registers_full_tool_surface():
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert len(names) >= 50
    for expected in {
        "spot_get_ticker",
        "spot_place_order",
        "futures_get_contracts",
        "futures_get_positions",
        "futures_place_order",
        "futures_cancel_all_orders",
        "futures_place_trigger_order",
        "htx_diagnose_private_access",
    }:
        assert expected in names


def test_mutation_tool_returns_dry_run_without_confirm():
    result = asyncio.run(
        mcp.call_tool(
            "futures_place_order",
            {
                "contract_code": "BTC-USDT",
                "volume": 1,
                "direction": "buy",
                "order_price_type": "limit",
                "price": 100,
            },
        )
    )
    assert result.structured_content["dry_run"] is True
    assert result.structured_content["executed"] is False


def test_spot_account_is_resolved_when_not_configured(monkeypatch):
    async def fake_private_get(path, **_query):
        assert path == "/v1/account/accounts"
        return {"status": "ok", "data": [{"id": 42, "type": "spot", "state": "working"}]}

    monkeypatch.setattr(server, "_private_get", fake_private_get)
    monkeypatch.setattr(server.client, "config", replace(server.client.config, spot_account_id=None))
    assert asyncio.run(server._resolve_spot_account_id(None)) == "42"

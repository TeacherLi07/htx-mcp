import asyncio
from dataclasses import replace
from urllib.parse import urlsplit

import anyio
import pytest
from mcp import ClientSession

import htx_mcp.server as server
from htx_mcp.server import mcp


class FakeResponse:
    status_code = 200

    def json(self):
        return {"status": "ok", "code": 200, "data": {"accepted": True}}


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse()

    async def aclose(self):
        pass


def _use_fake_client(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(server.client, "_http", fake)
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
    return fake


async def _call_through_mcp_client(tool_name, arguments):
    client_to_server_send, client_to_server_receive = anyio.create_memory_object_stream(
        10
    )
    server_to_client_send, server_to_client_receive = anyio.create_memory_object_stream(
        10
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
            await mcp_client.initialize()
            result = await mcp_client.call_tool(tool_name, arguments)
        task_group.cancel_scope.cancel()
    return result


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


def test_toolsets_can_publish_only_the_selected_semantic_layer(monkeypatch):
    monkeypatch.setenv("HTX_TOOLSETS", "analysis")
    local = server.HtxMcpServer("toolset-test")

    @local.tool(annotations=server.READ, toolsets={"analysis"})
    async def allowed_tool() -> dict[str, str]:
        """A read-only analysis test tool with a useful description."""

        return {"ok": "yes"}

    @local.tool(annotations=server.READ, toolsets={"advanced"})
    async def hidden_tool() -> dict[str, str]:
        """An advanced test tool that should not be published in analysis mode."""

        return {"ok": "yes"}

    names = {tool.name for tool in asyncio.run(local.list_tools())}
    assert "allowed_tool" in names
    assert "hidden_tool" not in names


def test_tool_metadata_describes_every_published_argument():
    tools = asyncio.run(mcp.list_tools())

    def has_description(schema, root):
        if "description" in schema:
            return True
        reference = schema.get("$ref")
        if reference:
            definition = root.get("$defs", {}).get(reference.rsplit("/", 1)[-1], {})
            return has_description(definition, root)
        return any(has_description(item, root) for item in schema.get("anyOf", []))

    assert all(tool.title for tool in tools)
    assert all(len(tool.description) >= 80 for tool in tools)
    for tool in tools:
        for schema in tool.input_schema.get("properties", {}).values():
            assert has_description(schema, tool.input_schema), tool.name

    batch_schema = next(
        tool for tool in tools if tool.name == "futures_place_batch_orders"
    )
    order_schema = batch_schema.input_schema["$defs"]["FuturesBatchOrder"]
    assert order_schema["required"] == [
        "contract_code",
        "volume",
        "direction",
        "order_price_type",
    ]
    assert all(
        has_description(schema, batch_schema.input_schema)
        for schema in order_schema["properties"].values()
    )

    spot_place = next(tool for tool in tools if tool.name == "spot_place_order")
    client_id_schema = spot_place.input_schema["properties"]["client_order_id"][
        "anyOf"
    ][0]
    assert client_id_schema["maxLength"] == 64
    assert client_id_schema["pattern"] == "^[A-Za-z0-9_-]+$"

    futures_place = next(tool for tool in tools if tool.name == "futures_place_order")
    futures_client_id_schema = futures_place.input_schema["properties"][
        "client_order_id"
    ]["anyOf"][0]
    assert futures_client_id_schema["type"] == "integer"
    assert futures_client_id_schema["maximum"] == 9223372036854775807

    semantic_outputs = {
        tool.name: tool.output_schema for tool in tools if tool.name.startswith("htx_")
    }
    assert set(semantic_outputs["htx_get_market_snapshot"]["properties"]) >= {
        "product",
        "instrument",
        "data",
        "warnings",
    }
    assert set(semantic_outputs["htx_validate_trade_intent"]["properties"]) >= {
        "plan_id",
        "status",
        "checks",
    }
    assert set(semantic_outputs["htx_submit_trade"]["properties"]) == {
        "validation",
        "execution",
    }


def test_trade_preflight_prompt_stays_read_only_and_uses_semantic_tools():
    prompt = asyncio.run(
        mcp.get_prompt(
            "trade_preflight",
            {
                "product": "swap",
                "instrument": "BTC-USDT",
                "action": "open",
                "side": "buy",
                "order_kind": "limit",
                "price": "60000",
                "stop_loss": "59000",
                "take_profit": "62000",
                "quantity": "1",
            },
        )
    )

    text = prompt.messages[0].content.text
    assert "htx_get_instrument_rules" in text
    assert "htx_get_risk_snapshot" in text
    assert "htx_validate_trade_intent" in text
    assert "htx_preview_trade" in text
    assert "do not call execution tools" in text
    assert "Do not set confirm=true" in text
    assert "- Product: swap" in text
    assert "- Action: open" in text


def test_readable_swap_enums_are_converted_to_htx_values(monkeypatch):
    fake = _use_fake_client(monkeypatch)

    trigger = asyncio.run(
        mcp.call_tool(
            "futures_place_trigger_order",
            {
                "contract_code": "BTC-USDT",
                "trigger_type": "greater_or_equal",
                "trigger_price": 100,
                "volume": 1,
                "direction": "buy",
                "order_price": 101,
            },
        )
    )
    assert trigger.structured_content["request"]["body"]["trigger_type"] == "ge"
    assert trigger.structured_content["dry_run"] is True

    mode = asyncio.run(
        mcp.call_tool(
            "futures_switch_position_mode",
            {"contract_code": "BTC-USDT", "position_mode": "hedged"},
        )
    )
    assert mode.structured_content["request"]["body"]["position_mode"] == "dual_side"
    assert not fake.calls


def test_idempotent_mutations_publish_accurate_annotations():
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

    for name in ("spot_dead_man_switch", "futures_switch_leverage"):
        annotations = tools[name].annotations
        assert annotations.read_only_hint is False
        assert annotations.destructive_hint is True
        assert annotations.idempotent_hint is True

    assert tools["spot_place_order"].annotations.idempotent_hint is False


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


def test_semantic_preview_preserves_exact_decimal_request_values(monkeypatch):
    _use_fake_client(monkeypatch)

    result = asyncio.run(
        mcp.call_tool(
            "htx_preview_trade",
            {
                "intent": {
                    "product": "swap",
                    "instrument": "BTC-USDT",
                    "action": "open",
                    "side": "buy",
                    "order_kind": "limit",
                    "quantity": "0.00000001",
                    "price": "60000.123456789012345678",
                }
            },
        )
    )

    request = result.structured_content["request"]
    assert request["volume"] == "0.00000001"
    assert request["price"] == "60000.123456789012345678"


def test_low_level_swap_dry_run_serializes_decimal_values_as_strings():
    result = asyncio.run(
        mcp.call_tool(
            "futures_place_order",
            {
                "contract_code": "BTC-USDT",
                "volume": "0.00000001",
                "direction": "buy",
                "order_price_type": "limit",
                "price": "60000.123456789012345678",
            },
        )
    )
    body = result.structured_content["request"]["body"]
    assert body["volume"] == "0.00000001"
    assert body["price"] == "60000.123456789012345678"


def test_low_level_swap_order_preserves_numeric_client_order_id():
    result = asyncio.run(
        mcp.call_tool(
            "futures_place_order",
            {
                "contract_code": "BTC-USDT",
                "volume": "1",
                "direction": "buy",
                "order_price_type": "limit",
                "price": "60000.1",
                "client_order_id": 123456,
            },
        )
    )

    assert result.structured_content["request"]["body"]["client_order_id"] == 123456


def test_spot_client_order_id_uses_htx_request_field():
    placement = asyncio.run(
        mcp.call_tool(
            "spot_place_order",
            {
                "symbol": "btcusdt",
                "order_type": "buy-limit",
                "amount": "0.001",
                "price": "60000.1",
                "account_id": "1000",
                "client_order_id": "strategy-order-1",
            },
        )
    )
    cancellation = asyncio.run(
        mcp.call_tool(
            "spot_cancel_by_client_id",
            {"client_order_id": "strategy-order-1"},
        )
    )

    assert placement.structured_content["request"]["body"]["client-order-id"] == (
        "strategy-order-1"
    )
    assert cancellation.structured_content["request"]["body"] == {
        "client-order-id": "strategy-order-1"
    }


def test_spot_account_is_resolved_when_not_configured(monkeypatch):
    async def fake_private_get(path, **_query):
        assert path == "/v1/account/accounts"
        return {
            "status": "ok",
            "data": [{"id": 42, "type": "spot", "state": "working"}],
        }

    monkeypatch.setattr(server, "_private_get", fake_private_get)
    monkeypatch.setattr(
        server.client, "config", replace(server.client.config, spot_account_id=None)
    )
    assert asyncio.run(server._resolve_spot_account_id(None)) == "42"


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "futures_get_history_orders",
            {"contract_code": "BTC-USDT", "size": 7},
        ),
        (
            "futures_get_match_results",
            {"contract_code": "BTC-USDT", "size": 7},
        ),
        (
            "futures_get_financial_records",
            {"contract_code": "BTC-USDT", "size": 7},
        ),
        (
            "futures_get_liquidation_orders",
            {"contract_code": "BTC-USDT", "size": 7},
        ),
    ],
)
def test_historical_futures_tools_use_current_v3_endpoints(
    monkeypatch, tool_name, arguments
):
    fake = _use_fake_client(monkeypatch)

    result = asyncio.run(mcp.call_tool(tool_name, arguments))

    assert result.is_error is False
    assert urlsplit(fake.calls[-1][1]).path.startswith("/linear-swap-api/v3/")
    assert fake.calls[-1][2]["json"]["size"] == 7


def test_cross_financial_records_use_the_cross_v3_endpoint(monkeypatch):
    fake = _use_fake_client(monkeypatch)

    result = asyncio.run(
        mcp.call_tool(
            "futures_get_financial_records",
            {"margin_mode": "cross", "size": 9},
        )
    )

    assert result.is_error is False
    assert (
        urlsplit(fake.calls[-1][1]).path
        == "/linear-swap-api/v3/swap_cross_financial_record"
    )
    assert fake.calls[-1][2]["json"] == {
        "mar_acct": "USDT",
        "direct": "prev",
        "size": 9,
    }


def test_invalid_tool_parameters_are_rejected_by_mcp_before_http(monkeypatch):
    fake = _use_fake_client(monkeypatch)

    invalid_calls = [
        (
            "futures_get_elite_ratios",
            {"contract_code": "BTC-USDT", "period": "1hour"},
            "period",
        ),
        (
            "futures_get_trigger_history",
            {"contract_code": "BTC-USDT", "create_date": 0},
            "create_date",
        ),
        (
            "spot_place_order",
            {
                "symbol": "btcusdt",
                "order_type": "buy-stop-limit",
                "amount": 1,
                "price": 100,
            },
            "stop_price",
        ),
        (
            "spot_cancel_by_client_id",
            {"client_order_id": "contains spaces"},
            "client_order_id",
        ),
        (
            "futures_place_order",
            {
                "contract_code": "BTC-USDT",
                "volume": 1,
                "direction": "buy",
                "price": 100,
                "client_order_id": "not-numeric",
            },
            "client_order_id",
        ),
        ("futures_get_liquidation_orders", {}, "contract_code"),
    ]

    for tool_name, arguments, expected_text in invalid_calls:
        result = asyncio.run(_call_through_mcp_client(tool_name, arguments))
        assert result.is_error is True
        assert expected_text in result.content[0].text

    assert fake.calls == []

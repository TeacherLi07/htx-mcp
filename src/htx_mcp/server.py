"""MCP server exposing HTX's official spot and USDT-margined swap APIs.

Every state-changing tool has two safety gates: the caller must pass
``confirm=true`` and the process must set ``HTX_ENABLE_TRADING=true``.
Otherwise it returns a dry-run preview and makes no mutation request.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .client import HtxClient, HtxConfig, ensure_confirmation

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("HTX_LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
# httpx's INFO log contains the signed URL (including AccessKeyId and
# Signature). Keep transport diagnostics off by default so credentials never
# leak into the MCP host's stderr log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

mcp = MCPServer(
    name="htx-official-api",
    version="0.1.0",
    description="HTX official REST API tools for market data, account inspection, and guarded trading.",
    instructions=(
        "Use read-only tools to inspect live state. Mutations are dry-run unless confirm=true and "
        "HTX_ENABLE_TRADING=true are both present. An order acknowledgement is not a fill; query status."
    ),
)
client = HtxClient(HtxConfig.from_env())

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def _q(**values: Any) -> dict[str, Any]:
    """Drop unset query fields while preserving valid falsey values."""

    return {key: value for key, value in values.items() if value is not None}


def _symbol(symbol: str) -> str:
    value = symbol.strip().lower()
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("symbol must be a non-empty symbol such as btcusdt")
    return value


def _contract(contract_code: str) -> str:
    value = contract_code.strip().upper()
    if not value or any(ch.isspace() for ch in value):
        raise ValueError("contract_code must be a non-empty code such as BTC-USDT")
    return value


def _page(page_index: int, page_size: int) -> tuple[int, int]:
    if page_index < 1:
        raise ValueError("page_index must be >= 1")
    if not 1 <= page_size <= 50:
        raise ValueError("page_size must be between 1 and 50")
    return page_index, page_size


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _base_url_for(path: str) -> str:
    """Route derivatives to HTX's derivatives host and spot calls to spot host."""

    if path.startswith("/linear-") or path.startswith("/index/"):
        return client.config.futures_base_url
    return client.config.base_url


async def _public(path: str, **query: Any) -> dict[str, Any]:
    return await client.request(
        "GET", path, query=_q(**query), private=False, base_url=_base_url_for(path)
    )


async def _private_get(path: str, **query: Any) -> dict[str, Any]:
    return await client.request(
        "GET", path, query=_q(**query), private=True, base_url=_base_url_for(path)
    )


async def _private_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await client.request(
        "POST", path, body=body or {}, private=True, base_url=_base_url_for(path)
    )


async def _mutation(
    tool_name: str,
    path: str,
    body: dict[str, Any] | None,
    confirm: bool,
    *,
    mode: str = "POST",
) -> dict[str, Any]:
    request = {"method": mode, "path": path, "body": body or {}}
    preview = ensure_confirmation(client, tool_name=tool_name, confirm=confirm, request=request)
    if preview is not None:
        return preview
    return await client.request(
        mode,
        path,
        body=body or {},
        private=True,
        base_url=_base_url_for(path),
    )


def _validate_spot_order(order_type: str, amount: float, price: float | None) -> None:
    allowed = {
        "buy-market", "sell-market", "buy-limit", "sell-limit", "buy-ioc", "sell-ioc",
        "buy-limit-maker", "sell-limit-maker", "buy-stop-limit", "sell-stop-limit",
        "buy-limit-fok", "sell-limit-fok", "buy-stop-limit-fok", "sell-stop-limit-fok",
    }
    if order_type not in allowed:
        raise ValueError(f"unsupported spot order type: {order_type}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    if "market" not in order_type and price is None:
        raise ValueError("price is required for non-market spot orders")


def _validate_futures_order(
    *,
    volume: float,
    direction: str,
    offset: str | None,
    order_price_type: str,
    price: float | None,
    lever_rate: int | None,
    tp_trigger_price: float | None,
    sl_trigger_price: float | None,
) -> None:
    if volume <= 0:
        raise ValueError("volume must be positive")
    if direction not in {"buy", "sell"}:
        raise ValueError("direction must be buy or sell")
    if offset is not None and offset not in {"open", "close", "both"}:
        raise ValueError("offset must be open, close, or both")
    allowed_price_types = {
        "limit", "opponent", "post_only", "optimal_5", "optimal_10", "optimal_20", "lightning",
        "fok", "ioc", "opponent_ioc", "lightning_ioc", "optimal_5_ioc", "optimal_10_ioc",
        "optimal_20_ioc", "opponent_fok", "lightning_fok", "optimal_5_fok", "optimal_10_fok",
        "optimal_20_fok",
    }
    if order_price_type not in allowed_price_types:
        raise ValueError(f"unsupported futures order_price_type: {order_price_type}")
    if order_price_type in {"limit", "post_only", "fok", "ioc"} and price is None:
        raise ValueError(f"price is required for order_price_type={order_price_type}")
    if lever_rate is not None and lever_rate <= 0:
        raise ValueError("lever_rate must be positive")
    if tp_trigger_price is not None and tp_trigger_price <= 0:
        raise ValueError("tp_trigger_price must be positive")
    if sl_trigger_price is not None and sl_trigger_price <= 0:
        raise ValueError("sl_trigger_price must be positive")


@mcp.resource("htx://configuration", name="configuration", mime_type="application/json")
def configuration_resource() -> str:
    """Expose non-secret server configuration for client diagnostics."""

    return _json(
        {
            "server": "htx-official-api",
            "api_base_url": client.config.base_url,
            "futures_api_base_url": client.config.futures_base_url,
            "credentials_configured": client.credentials_configured,
            "trading_enabled": client.config.enable_trading,
            "spot_account_id_configured": bool(client.config.spot_account_id),
            "supported_products": ["spot", "usdt-margined-swap"],
            "safety": {
                "mutations_require_confirm": True,
                "mutations_require_HTX_ENABLE_TRADING": True,
                "server_logs_to": "stderr",
            },
        }
    )


@mcp.prompt(name="trade_preflight", title="HTX trade preflight")
def trade_preflight_prompt(
    symbol_or_contract: str,
    side: str,
    entry_price: str,
    stop_loss: str,
    take_profit: str,
    quantity: str,
) -> str:
    """Create a compact checklist to review before enabling a mutation tool."""

    return (
        "Before submitting this HTX trade, verify live metadata and account state:\n"
        f"- Instrument: {symbol_or_contract}\n- Side: {side}\n- Entry: {entry_price}\n"
        f"- Stop loss: {stop_loss}\n- Take profit: {take_profit}\n- Quantity: {quantity}\n"
        "- Confirm contract precision/minimum, available balance, leverage, existing positions/orders, "
        "and stop/target direction. Use confirm=true only after this review."
    )


# ---------------------------------------------------------------------------
# Spot market and metadata
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def spot_get_ticker(symbol: str) -> dict[str, Any]:
    """Get the latest merged ticker for one spot symbol, for example btcusdt."""

    return await _public("/market/detail/merged", symbol=_symbol(symbol))


@mcp.tool(annotations=READ)
async def spot_get_tickers() -> dict[str, Any]:
    """Get merged tickers for all supported spot symbols."""

    return await _public("/market/tickers")


@mcp.tool(annotations=READ)
async def spot_get_klines(
    symbol: str,
    period: Literal["1min", "5min", "15min", "30min", "60min", "4hour", "1day", "1week", "1mon"] = "1day",
    size: int = 100,
    from_time: int | None = None,
    to_time: int | None = None,
) -> dict[str, Any]:
    """Get historical spot candlesticks; size is limited by HTX to 1..2000."""

    if not 1 <= size <= 2000:
        raise ValueError("size must be between 1 and 2000")
    return await _public(
        "/market/history/kline",
        symbol=_symbol(symbol),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@mcp.tool(annotations=READ)
async def spot_get_depth(
    symbol: str,
    depth_type: Literal["step0", "step1", "step2", "step3", "step4", "step5"] = "step0",
    depth: Literal[5, 10, 20] = 20,
) -> dict[str, Any]:
    """Get spot order-book depth."""

    return await _public("/market/depth", symbol=_symbol(symbol), type=depth_type, depth=depth)


@mcp.tool(annotations=READ)
async def spot_get_recent_trades(symbol: str, size: int = 100) -> dict[str, Any]:
    """Get the latest spot trades, with size between 1 and 2000."""

    if not 1 <= size <= 2000:
        raise ValueError("size must be between 1 and 2000")
    return await _public("/market/history/trade", symbol=_symbol(symbol), size=size)


@mcp.tool(annotations=READ)
async def spot_get_symbols() -> dict[str, Any]:
    """Get spot symbol metadata, precision, minimums, and trading status."""

    return await _public("/v1/common/symbols")


@mcp.tool(annotations=READ)
async def spot_get_currencies() -> dict[str, Any]:
    """Get spot currency metadata."""

    return await _public("/v1/common/currencys")


@mcp.tool(annotations=READ)
async def spot_get_currency_reference(currency: str | None = None, authorized_user: bool = False) -> dict[str, Any]:
    """Get V2 currency and chain reference data."""

    return await _public("/v2/reference/currencies", currency=currency, authorizedUser=authorized_user)


@mcp.tool(annotations=READ)
async def spot_get_market_status() -> dict[str, Any]:
    """Get HTX spot market status."""

    return await _public("/v2/market-status")


@mcp.tool(annotations=READ)
async def spot_get_server_timestamp() -> dict[str, Any]:
    """Get HTX server time."""

    return await _public("/v1/common/timestamp")


# ---------------------------------------------------------------------------
# Spot account and read-only order inspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def spot_get_accounts() -> dict[str, Any]:
    """List authenticated spot, margin, and other account IDs."""

    return await _private_get("/v1/account/accounts")


@mcp.tool(annotations=READ)
async def spot_get_account_balance(account_id: str | None = None) -> dict[str, Any]:
    """Get balances for a spot account; omit account_id if HTX_SPOT_ACCOUNT_ID is set."""

    resolved = account_id or client.config.spot_account_id
    if not resolved:
        raise ValueError("account_id is required or set HTX_SPOT_ACCOUNT_ID")
    return await _private_get(f"/v1/account/accounts/{resolved}/balance")


@mcp.tool(annotations=READ)
async def spot_get_open_orders(
    account_id: str | None = None,
    symbol: str | None = None,
    size: int = 100,
    direct: Literal["next", "prev"] | None = None,
    from_order_id: str | None = None,
) -> dict[str, Any]:
    """Get open spot orders, optionally filtered by symbol and pagination."""

    resolved = account_id or client.config.spot_account_id
    if not resolved:
        raise ValueError("account_id is required or set HTX_SPOT_ACCOUNT_ID")
    if not 1 <= size <= 1000:
        raise ValueError("size must be between 1 and 1000")
    return await _private_get(
        "/v1/order/openOrders",
        **{
            "account-id": resolved,
            "symbol": _symbol(symbol) if symbol else None,
            "size": size,
            "direct": direct,
            "from": from_order_id,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_order(order_id: str) -> dict[str, Any]:
    """Get a spot order by exchange order ID."""

    if not order_id.strip():
        raise ValueError("order_id is required")
    return await _private_get(f"/v1/order/orders/{order_id.strip()}")


@mcp.tool(annotations=READ)
async def spot_get_order_by_client_id(client_order_id: str) -> dict[str, Any]:
    """Get a spot order by client order ID."""

    return await _private_get("/v1/order/orders/getClientOrder", clientOrderId=client_order_id)


@mcp.tool(annotations=READ)
async def spot_get_match_results(
    symbol: str,
    types: str | None = None,
    size: int = 100,
    start_time: int | None = None,
    end_time: int | None = None,
    from_match_id: str | None = None,
    direct: Literal["next", "prev"] = "next",
) -> dict[str, Any]:
    """Get spot fills/match results for a symbol."""

    if not 1 <= size <= 500:
        raise ValueError("size must be between 1 and 500")
    return await _private_get(
        "/v1/order/matchresults",
        **{
            "symbol": _symbol(symbol),
            "types": types,
            "size": size,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_match_id,
            "direct": direct,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_order_match_results(order_id: str) -> dict[str, Any]:
    """Get fills for one spot order by exchange order ID."""

    order_id = order_id.strip()
    if not order_id:
        raise ValueError("order_id is required")
    return await _private_get(f"/v1/order/orders/{order_id}/matchresults")


@mcp.tool(annotations=READ)
async def spot_get_history_orders(
    symbol: str,
    states: str = "filled,canceled,partial-canceled",
    types: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    from_order_id: str | None = None,
    direct: Literal["next", "prev"] = "next",
    size: int = 100,
) -> dict[str, Any]:
    """Search spot orders over a maximum 48-hour window using official history filters."""

    if not 1 <= size <= 100:
        raise ValueError("size must be between 1 and 100")
    if not states.strip():
        raise ValueError("states must contain at least one order state")
    return await _private_get(
        "/v1/order/orders",
        **{
            "symbol": _symbol(symbol),
            "states": states,
            "types": types,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_order_id,
            "direct": direct,
            "size": size,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_recent_history_orders(
    symbol: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    direct: Literal["next", "prev"] = "next",
    size: int = 100,
) -> dict[str, Any]:
    """Search spot orders in the short history window supported by HTX."""

    if not 10 <= size <= 1000:
        raise ValueError("size must be between 10 and 1000")
    return await _private_get(
        "/v1/order/history",
        **{
            "symbol": _symbol(symbol) if symbol else None,
            "start-time": start_time,
            "end-time": end_time,
            "direct": direct,
            "size": size,
        },
    )


@mcp.tool(annotations=WRITE)
async def spot_dead_man_switch(timeout_seconds: int, confirm: bool = False) -> dict[str, Any]:
    """Arm or disarm HTX's spot dead-man switch; use 0 to disarm, >=5 to arm."""

    if timeout_seconds != 0 and timeout_seconds < 5:
        raise ValueError("timeout_seconds must be 0 or at least 5")
    return await _mutation(
        "spot_dead_man_switch",
        "/v2/algo-orders/cancel-all-after",
        {"timeout": timeout_seconds},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_place_order(
    symbol: str,
    order_type: str,
    amount: float,
    account_id: str | None = None,
    price: float | None = None,
    client_order_id: str | None = None,
    source: Literal["spot-api"] = "spot-api",
    stop_price: float | None = None,
    operator: Literal["gte", "lte"] | None = None,
    self_match_prevent: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Place a spot order; dry-run until confirm=true and HTX_ENABLE_TRADING=true."""

    resolved = account_id or client.config.spot_account_id
    if not resolved:
        raise ValueError("account_id is required or set HTX_SPOT_ACCOUNT_ID")
    _validate_spot_order(order_type, amount, price)
    body = _q(
        **{
            "account-id": resolved,
            "symbol": _symbol(symbol),
            "type": order_type,
            "amount": str(amount),
            "price": str(price) if price is not None else None,
            "source": source,
            "client-order-id": client_order_id,
            "self-match-prevent": 1 if self_match_prevent else 0,
            "stop-price": str(stop_price) if stop_price is not None else None,
            "operator": operator,
        }
    )
    return await _mutation("spot_place_order", "/v1/order/orders/place", body, confirm)


@mcp.tool(annotations=WRITE)
async def spot_cancel_order(order_id: str, confirm: bool = False) -> dict[str, Any]:
    """Submit cancellation of one spot order by exchange order ID."""

    order_id = order_id.strip()
    if not order_id:
        raise ValueError("order_id is required")
    return await _mutation(
        "spot_cancel_order",
        f"/v1/order/orders/{order_id}/submitcancel",
        {},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_cancel_by_client_id(client_order_id: str, confirm: bool = False) -> dict[str, Any]:
    """Submit cancellation of one spot order by client order ID."""

    if not client_order_id.strip():
        raise ValueError("client_order_id is required")
    return await _mutation(
        "spot_cancel_by_client_id",
        "/v1/order/orders/submitcancelclientorder",
        {"client-order-id": client_order_id.strip()},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_cancel_orders_by_ids(order_ids: list[str], confirm: bool = False) -> dict[str, Any]:
    """Submit cancellation for up to 50 spot order IDs."""

    if not 1 <= len(order_ids) <= 50:
        raise ValueError("order_ids must contain between 1 and 50 IDs")
    return await _mutation("spot_cancel_orders_by_ids", "/v1/order/orders/batchcancel", {"order-ids": order_ids}, confirm)


@mcp.tool(annotations=WRITE)
async def spot_cancel_open_orders(
    account_id: str | None = None,
    symbol: str | None = None,
    order_types: list[str] | None = None,
    side: Literal["buy", "sell"] | None = None,
    size: int = 100,
    confirm: bool = False,
) -> dict[str, Any]:
    """Submit cancellation for open spot orders matching criteria."""

    resolved = account_id or client.config.spot_account_id
    if not resolved:
        raise ValueError("account_id is required or set HTX_SPOT_ACCOUNT_ID")
    if not 1 <= size <= 100:
        raise ValueError("size must be between 1 and 100")
    body = _q(
        **{
            "account-id": resolved,
            "symbol": ",".join(_symbol(item) for item in symbol.split(",")) if symbol else None,
            "types": ",".join(order_types) if order_types else None,
            "side": side,
            "size": size,
        }
    )
    return await _mutation("spot_cancel_open_orders", "/v1/order/orders/batchCancelOpenOrders", body, confirm)


# ---------------------------------------------------------------------------
# USDT-margined swap market data
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def futures_get_contracts(contract_code: str | None = None) -> dict[str, Any]:
    """Get USDT-margined swap/futures contract metadata and trading rules."""

    return await _public(
        "/linear-swap-api/v1/swap_contract_info",
        contract_code=_contract(contract_code) if contract_code else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_ticker(contract_code: str) -> dict[str, Any]:
    """Get a merged ticker for one USDT-margined contract."""

    return await _public("/linear-swap-ex/market/detail/merged", contract_code=_contract(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_tickers(
    contract_code: str | None = None,
    business_type: Literal["swap", "futures", "all"] = "swap",
) -> dict[str, Any]:
    """Get a batch of USDT-margined contract tickers."""

    return await _public(
        "/linear-swap-ex/market/detail/batch_merged",
        contract_code=_contract(contract_code) if contract_code else None,
        business_type=business_type,
    )


@mcp.tool(annotations=READ)
async def futures_get_depth(
    contract_code: str,
    depth_type: str = "step0",
) -> dict[str, Any]:
    """Get USDT-margined contract order-book depth."""

    allowed = {f"step{i}" for i in range(0, 20)}
    if depth_type not in allowed:
        raise ValueError("depth_type must be a supported HTX step0..step19 value")
    return await _public(
        "/linear-swap-ex/market/depth",
        contract_code=_contract(contract_code),
        type=depth_type,
    )


@mcp.tool(annotations=READ)
async def futures_get_klines(
    contract_code: str,
    period: Literal["1min", "5min", "15min", "30min", "60min", "4hour", "1day", "1week", "1mon"] = "1day",
    size: int = 100,
    from_time: int | None = None,
    to_time: int | None = None,
) -> dict[str, Any]:
    """Get historical USDT-margined contract candlesticks."""

    if not 1 <= size <= 2000:
        raise ValueError("size must be between 1 and 2000")
    return await _public(
        "/linear-swap-ex/market/history/kline",
        contract_code=_contract(contract_code),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@mcp.tool(annotations=READ)
async def futures_get_recent_trades(contract_code: str, size: int = 100) -> dict[str, Any]:
    """Get recent trades for one USDT-margined contract."""

    if not 1 <= size <= 2000:
        raise ValueError("size must be between 1 and 2000")
    return await _public(
        "/linear-swap-ex/market/history/trade",
        contract_code=_contract(contract_code),
        size=size,
    )


@mcp.tool(annotations=READ)
async def futures_get_index(contract_code: str) -> dict[str, Any]:
    """Get index price information for a USDT-margined contract."""

    return await _public("/linear-swap-api/v1/swap_index", contract_code=_contract(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_price_limit(contract_code: str) -> dict[str, Any]:
    """Get the current price limits for a USDT-margined contract."""

    return await _public("/linear-swap-api/v1/swap_price_limit", contract_code=_contract(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_open_interest(contract_code: str | None = None) -> dict[str, Any]:
    """Get current open interest for one or all USDT-margined contracts."""

    return await _public(
        "/linear-swap-api/v1/swap_open_interest",
        contract_code=_contract(contract_code) if contract_code else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_funding_rate(contract_code: str) -> dict[str, Any]:
    """Get the current funding rate for a USDT-margined contract."""

    return await _public("/linear-swap-api/v1/swap_funding_rate", contract_code=_contract(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_batch_funding_rate(contract_codes: list[str] | None = None) -> dict[str, Any]:
    """Get funding rates for multiple USDT-margined contracts."""

    if contract_codes and len(contract_codes) > 50:
        raise ValueError("contract_codes may contain at most 50 codes")
    return await _public(
        "/linear-swap-api/v1/swap_batch_funding_rate",
        contract_code=",".join(_contract(item) for item in contract_codes) if contract_codes else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_historical_funding_rate(
    contract_code: str,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get historical funding rates for a USDT-margined contract."""

    _page(page_index, page_size)
    return await _public(
        "/linear-swap-api/v1/swap_historical_funding_rate",
        contract_code=_contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@mcp.tool(annotations=READ)
async def futures_get_api_state() -> dict[str, Any]:
    """Get HTX USDT-margined swap service status."""

    return await _public("/linear-swap-api/v1/swap_api_state")


@mcp.tool(annotations=READ)
async def futures_get_risk_info(contract_code: str | None = None) -> dict[str, Any]:
    """Get contract insurance-fund and estimated clawback information."""

    return await _public(
        "/linear-swap-api/v1/swap_risk_info",
        contract_code=_contract(contract_code) if contract_code else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_insurance_fund(
    contract_code: str,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get historical insurance-fund records for a USDT-margined contract."""

    _page(page_index, page_size)
    return await _public(
        "/linear-swap-api/v1/swap_insurance_fund",
        contract_code=_contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@mcp.tool(annotations=READ)
async def futures_get_liquidation_orders(
    contract_code: str | None = None,
    trade_type: int = 0,
    create_date: int | None = None,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get public liquidation-order records."""

    _page(page_index, page_size)
    return await _public(
        "/linear-swap-api/v1/swap_liquidation_orders",
        contract_code=_contract(contract_code) if contract_code else None,
        trade_type=trade_type,
        create_date=create_date,
        page_index=page_index,
        page_size=page_size,
    )


@mcp.tool(annotations=READ)
async def futures_get_elite_ratios(
    contract_code: str,
    period: Literal["5min", "15min", "30min", "1hour", "4hour", "1day"] = "1hour",
) -> dict[str, Any]:
    """Get top-trader account and position sentiment ratios."""

    code = _contract(contract_code)
    return {
        "account_ratio": await _public(
            "/linear-swap-api/v1/swap_elite_account_ratio",
            contract_code=code,
            period=period,
        ),
        "position_ratio": await _public(
            "/linear-swap-api/v1/swap_elite_position_ratio",
            contract_code=code,
            period=period,
        ),
    }


def _swap_endpoint(stem: str, margin_mode: Literal["isolated", "cross"]) -> str:
    prefix = "swap_" if margin_mode == "isolated" else "swap_cross_"
    return f"/linear-swap-api/v1/{prefix}{stem}"


def _swap_body(contract_code: str | None = None, **values: Any) -> dict[str, Any]:
    return _q(contract_code=_contract(contract_code) if contract_code else None, **values)


# ---------------------------------------------------------------------------
# USDT-margined swap account and order inspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def futures_get_account_info(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
) -> dict[str, Any]:
    """Get authenticated USDT-margined account equity and margin information."""

    return await _private_post(_swap_endpoint("account_info", margin_mode), _swap_body(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_positions(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
) -> dict[str, Any]:
    """Get authenticated USDT-margined positions."""

    return await _private_post(_swap_endpoint("position_info", margin_mode), _swap_body(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_account_position_info(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
) -> dict[str, Any]:
    """Get combined USDT-margined assets and positions."""

    stem = "account_position_info"
    return await _private_post(_swap_endpoint(stem, margin_mode), _swap_body(contract_code))


@mcp.tool(annotations=READ)
async def futures_get_available_leverage(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
) -> dict[str, Any]:
    """Get leverage values currently available for a contract/account."""

    return await _private_post(
        _swap_endpoint("available_level_rate", margin_mode),
        _swap_body(contract_code),
    )


@mcp.tool(annotations=READ)
async def futures_get_open_orders(
    contract_code: str,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    page_index: int = 1,
    page_size: int = 20,
    sort_by: Literal["created_at", "update_time"] = "created_at",
    trade_type: int = 0,
) -> dict[str, Any]:
    """Get current unfilled USDT-margined orders."""

    _page(page_index, page_size)
    body = _swap_body(
        contract_code,
        page_index=page_index,
        page_size=page_size,
        sort_by=sort_by,
        trade_type=trade_type,
    )
    return await _private_post(_swap_endpoint("openorders", margin_mode), body)


@mcp.tool(annotations=READ)
async def futures_get_order_info(
    contract_code: str,
    order_id: str,
    margin_mode: Literal["isolated", "cross"] = "isolated",
) -> dict[str, Any]:
    """Get current/status information for one USDT-margined order."""

    if not order_id.strip():
        raise ValueError("order_id is required")
    return await _private_post(
        _swap_endpoint("order_info", margin_mode),
        _swap_body(contract_code, order_id=order_id.strip()),
    )


@mcp.tool(annotations=READ)
async def futures_get_order_detail(
    contract_code: str,
    order_id: str,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    created_at: int | None = None,
    order_type: int | None = None,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get fills and execution details for one USDT-margined order."""

    _page(page_index, page_size)
    return await _private_post(
        _swap_endpoint("order_detail", margin_mode),
        _swap_body(
            contract_code,
            order_id=order_id.strip(),
            created_at=created_at,
            order_type=order_type,
            page_index=page_index,
            page_size=page_size,
        ),
    )


@mcp.tool(annotations=READ)
async def futures_get_history_orders(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
    trade_type: int = 0,
    type: int = 1,
    status: str | None = None,
    create_date: int | None = None,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get historical USDT-margined orders; use narrow filters for faster results."""

    _page(page_index, page_size)
    body = _swap_body(
        contract_code,
        trade_type=trade_type,
        type=type,
        status=status,
        create_date=create_date,
        page_index=page_index,
        page_size=page_size,
    )
    return await _private_post(_swap_endpoint("hisorders", margin_mode), body)


@mcp.tool(annotations=READ)
async def futures_get_match_results(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    contract_code: str | None = None,
    trade_type: int = 0,
    create_date: int | None = None,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get historical USDT-margined fills/match results."""

    _page(page_index, page_size)
    body = _swap_body(
        contract_code,
        trade_type=trade_type,
        create_date=create_date,
        page_index=page_index,
        page_size=page_size,
    )
    return await _private_post(_swap_endpoint("matchresults", margin_mode), body)


@mcp.tool(annotations=READ)
async def futures_get_financial_records(
    margin_mode: Literal["isolated", "cross"] = "isolated",
    symbol: str | None = None,
    type: int | None = None,
    create_date: int | None = None,
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get USDT-margined account financial records."""

    _page(page_index, page_size)
    body = _q(
        symbol=symbol.upper() if symbol else None,
        type=type,
        create_date=create_date,
        page_index=page_index,
        page_size=page_size,
    )
    return await _private_post(_swap_endpoint("financial_record", margin_mode), body)


# ---------------------------------------------------------------------------
# USDT-margined swap trading and strategy orders
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
async def futures_place_order(
    contract_code: str,
    volume: float,
    direction: Literal["buy", "sell"],
    order_price_type: str = "limit",
    price: float | None = None,
    offset: Literal["open", "close", "both"] | None = None,
    lever_rate: int | None = None,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    reduce_only: bool = False,
    client_order_id: str | None = None,
    tp_trigger_price: float | None = None,
    tp_order_price: float | None = None,
    tp_order_price_type: str = "optimal_5",
    sl_trigger_price: float | None = None,
    sl_order_price: float | None = None,
    sl_order_price_type: str = "optimal_5",
    confirm: bool = False,
) -> dict[str, Any]:
    """Place a USDT-margined order with optional exchange-side TP/SL protection."""

    _validate_futures_order(
        volume=volume,
        direction=direction,
        offset=offset,
        order_price_type=order_price_type,
        price=price,
        lever_rate=lever_rate,
        tp_trigger_price=tp_trigger_price,
        sl_trigger_price=sl_trigger_price,
    )
    body = _swap_body(
        contract_code,
        volume=volume,
        direction=direction,
        offset=offset,
        lever_rate=lever_rate,
        price=price,
        order_price_type=order_price_type,
        reduce_only=1 if reduce_only else 0,
        client_order_id=client_order_id,
        tp_trigger_price=tp_trigger_price,
        tp_order_price=tp_order_price,
        tp_order_price_type=tp_order_price_type if tp_trigger_price is not None else None,
        sl_trigger_price=sl_trigger_price,
        sl_order_price=sl_order_price,
        sl_order_price_type=sl_order_price_type if sl_trigger_price is not None else None,
    )
    return await _mutation(
        "futures_place_order",
        _swap_endpoint("order", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_place_batch_orders(
    orders: list[dict[str, Any]],
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Place a batch of USDT-margined orders using official orders_data fields."""

    if not 1 <= len(orders) <= 10:
        raise ValueError("orders must contain between 1 and 10 order objects")
    for index, order in enumerate(orders):
        missing = [key for key in ("contract_code", "volume", "direction", "order_price_type") if key not in order]
        if missing:
            raise ValueError(f"orders[{index}] is missing required fields: {', '.join(missing)}")
        _validate_futures_order(
            volume=float(order["volume"]),
            direction=str(order["direction"]),
            offset=order.get("offset"),
            order_price_type=str(order["order_price_type"]),
            price=float(order["price"]) if order.get("price") is not None else None,
            lever_rate=int(order["lever_rate"]) if order.get("lever_rate") is not None else None,
            tp_trigger_price=float(order["tp_trigger_price"]) if order.get("tp_trigger_price") is not None else None,
            sl_trigger_price=float(order["sl_trigger_price"]) if order.get("sl_trigger_price") is not None else None,
        )
    normalized = [
        {
            **order,
            "contract_code": _contract(str(order["contract_code"])),
            "volume": order["volume"],
            "reduce_only": int(bool(order.get("reduce_only", False))),
        }
        for order in orders
    ]
    body = {"orders_data": normalized}
    return await _mutation(
        "futures_place_batch_orders",
        _swap_endpoint("batchorder", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_order(
    contract_code: str,
    order_id: str | None = None,
    client_order_id: str | None = None,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Cancel one USDT-margined order by order ID or client order ID."""

    if not order_id and not client_order_id:
        raise ValueError("order_id or client_order_id is required")
    body = _swap_body(
        contract_code,
        order_id=order_id,
        client_order_id=client_order_id,
    )
    return await _mutation("futures_cancel_order", _swap_endpoint("cancel", margin_mode), body, confirm)


@mcp.tool(annotations=WRITE)
async def futures_cancel_all_orders(
    contract_code: str,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Cancel all open USDT-margined orders for one contract."""

    return await _mutation(
        "futures_cancel_all_orders",
        _swap_endpoint("cancelall", margin_mode),
        _swap_body(contract_code),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_switch_leverage(
    contract_code: str,
    lever_rate: int,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Switch USDT-margined leverage; HTX may reject this while orders/positions exist."""

    if lever_rate <= 0:
        raise ValueError("lever_rate must be positive")
    return await _mutation(
        "futures_switch_leverage",
        _swap_endpoint("switch_lever_rate", margin_mode),
        _swap_body(contract_code, lever_rate=lever_rate),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_lightning_close_position(
    contract_code: str,
    volume: float,
    direction: Literal["buy", "sell"],
    margin_mode: Literal["isolated", "cross"] = "isolated",
    order_price_type: Literal["market", "lightning_fok", "lightning_ioc"] = "market",
    client_order_id: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Submit HTX's lightning close-position order for a USDT-margined position."""

    if volume <= 0:
        raise ValueError("volume must be positive")
    if margin_mode == "isolated":
        path = "/linear-swap-api/v1/swap_lightning_close_position"
    else:
        path = "/linear-swap-api/v1/swap_cross_lightning_close_position"
    body = _swap_body(
        contract_code,
        volume=volume,
        direction=direction,
        order_price_type=order_price_type,
        client_order_id=client_order_id,
    )
    return await _mutation("futures_lightning_close_position", path, body, confirm)


@mcp.tool(annotations=WRITE)
async def futures_place_trigger_order(
    contract_code: str,
    trigger_type: Literal["ge", "le"],
    trigger_price: float,
    volume: float,
    direction: Literal["buy", "sell"],
    order_price_type: str = "limit",
    order_price: float | None = None,
    offset: Literal["open", "close", "both"] | None = None,
    lever_rate: int | None = None,
    reduce_only: bool = False,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Place a USDT-margined trigger/strategy order."""

    if trigger_price <= 0 or volume <= 0:
        raise ValueError("trigger_price and volume must be positive")
    if order_price_type == "limit" and order_price is None:
        raise ValueError("order_price is required for limit trigger orders")
    body = _swap_body(
        contract_code,
        trigger_type=trigger_type,
        trigger_price=trigger_price,
        order_price=order_price,
        order_price_type=order_price_type,
        volume=volume,
        direction=direction,
        offset=offset,
        lever_rate=lever_rate,
        reduce_only=1 if reduce_only else 0,
    )
    return await _mutation(
        "futures_place_trigger_order",
        _swap_endpoint("trigger_order", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=READ)
async def futures_get_trigger_open_orders(
    contract_code: str | None = None,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get open USDT-margined trigger orders."""

    _page(page_index, page_size)
    return await _private_post(
        _swap_endpoint("trigger_openorders", margin_mode),
        _swap_body(contract_code, page_index=page_index, page_size=page_size),
    )


@mcp.tool(annotations=READ)
async def futures_get_trigger_history(
    contract_code: str | None = None,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    page_index: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Get historical USDT-margined trigger orders."""

    _page(page_index, page_size)
    return await _private_post(
        _swap_endpoint("trigger_hisorders", margin_mode),
        _swap_body(contract_code, page_index=page_index, page_size=page_size),
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_trigger_order(
    contract_code: str,
    order_id: str,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Cancel one USDT-margined trigger order."""

    if not order_id.strip():
        raise ValueError("order_id is required")
    return await _mutation(
        "futures_cancel_trigger_order",
        _swap_endpoint("trigger_cancel", margin_mode),
        _swap_body(contract_code, order_id=order_id.strip()),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_all_trigger_orders(
    contract_code: str | None = None,
    direction: Literal["buy", "sell"] | None = None,
    offset: Literal["open", "close"] | None = None,
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Cancel all matching USDT-margined trigger orders."""

    return await _mutation(
        "futures_cancel_all_trigger_orders",
        _swap_endpoint("trigger_cancelall", margin_mode),
        _swap_body(contract_code, direction=direction, offset=offset),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_switch_position_mode(
    contract_code: str,
    position_mode: Literal["single_side", "dual_side"],
    margin_mode: Literal["isolated", "cross"] = "isolated",
    confirm: bool = False,
) -> dict[str, Any]:
    """Switch one USDT-margined contract between one-way and hedge position mode."""

    return await _mutation(
        "futures_switch_position_mode",
        _swap_endpoint("switch_position_mode", margin_mode),
        _swap_body(contract_code, position_mode=position_mode),
        confirm,
    )


@mcp.tool(annotations=READ)
async def futures_get_balance_valuation(
    valuation_asset: Literal["BTC", "USD", "USDT", "CNY", "EUR", "GBP", "VND", "HKD", "TWD", "MYR", "SGD", "KRW", "RUB", "TRY"] = "USDT",
) -> dict[str, Any]:
    """Get authenticated USDT-margined account valuation."""

    return await _private_post("/linear-swap-api/v1/swap_balance_valuation", {"valuation_asset": valuation_asset})


@mcp.tool(annotations=READ)
async def futures_get_api_trading_status() -> dict[str, Any]:
    """Get the authenticated API trading-status indicator for the swap account."""

    return await _private_get("/linear-swap-api/v1/swap_api_trading_status")


def main() -> None:
    """Run the MCP server; stdio is the default transport for desktop hosts."""

    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    logger.info("HTX MCP server starting on %s transport", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()

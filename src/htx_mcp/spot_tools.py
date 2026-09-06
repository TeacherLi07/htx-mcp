"""Low-level HTX tools for the spot API domain."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import server
from .precision import decimal_to_text

# Spot market and metadata
# ---------------------------------------------------------------------------


@server.mcp.tool(annotations=server.READ)
async def spot_get_ticker(symbol: server.SpotSymbol) -> dict[str, Any]:
    """Read the latest merged ticker for one HTX spot symbol.

    Use this for the current last price, best bid/ask, 24-hour volume, and price statistics.
    Returns the raw HTX JSON envelope; call spot_get_symbols first when precision or minimums matter.
    """

    return await server._public("/market/detail/merged", symbol=server._symbol(symbol))


@server.mcp.tool(annotations=server.READ)
async def spot_get_tickers() -> dict[str, Any]:
    """Read merged tickers for every currently supported HTX spot symbol.

    Use this for a market-wide snapshot. The response can be large; use spot_get_ticker for one symbol.
    """

    return await server._public("/market/tickers")


@server.mcp.tool(annotations=server.READ)
async def spot_get_klines(
    symbol: server.SpotSymbol,
    period: server.CandlePeriod = "1day",
    size: Annotated[
        int,
        Field(
            ge=1, le=2000, description="Number of candles to return; HTX allows 1-2000."
        ),
    ] = 100,
    from_time: server.MillisecondTimestamp | None = None,
    to_time: server.MillisecondTimestamp | None = None,
) -> dict[str, Any]:
    """Read historical OHLCV candlesticks for one HTX spot symbol.

    ``from_time`` and ``to_time`` are optional Unix milliseconds; ``size`` is 1-2000.
    The response is HTX's raw kline envelope, ordered according to the exchange API.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    server._validate_time_range(from_time, to_time, max_age_ms=None)
    return await server._public(
        "/market/history/kline",
        symbol=server._symbol(symbol),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_depth(
    symbol: server.SpotSymbol,
    depth_type: server.SpotDepthType = "step0",
    depth: Annotated[
        Literal[5, 10, 20],
        Field(description="Number of bid and ask levels to return: 5, 10, or 20."),
    ] = 20,
) -> dict[str, Any]:
    """Read the current spot order book for one symbol.

    ``depth_type`` controls price aggregation and ``depth`` controls the number of bid/ask levels.
    This is public market data and does not require API credentials.
    """

    return await server._public(
        "/market/depth", symbol=server._symbol(symbol), type=depth_type, depth=depth
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_recent_trades(
    symbol: server.SpotSymbol,
    size: Annotated[
        int,
        Field(
            ge=1,
            le=2000,
            description="Number of recent trades to return; HTX allows 1-2000.",
        ),
    ] = 100,
) -> dict[str, Any]:
    """Read the most recent public trades for one HTX spot symbol.

    Set ``size`` to the number of records needed, from 1 through 2000.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    return await server._public(
        "/market/history/trade", symbol=server._symbol(symbol), size=size
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_symbols() -> dict[str, Any]:
    """Read HTX spot symbol metadata and trading rules.

    Use the response to check price/amount precision, minimum order sizes, market limits, and whether a symbol is trading before placing an order.
    """

    return await server._public("/v1/common/symbols")


@server.mcp.tool(annotations=server.READ)
async def spot_get_currencies() -> dict[str, Any]:
    """Read HTX spot currency settings.

    Use this for currency precision, withdrawal/deposit settings, and currency status; chain-level details are available from spot_get_currency_reference.
    """

    return await server._public("/v1/common/currencys")


@server.mcp.tool(annotations=server.READ)
async def spot_get_currency_reference(
    currency: Annotated[
        server.CurrencyCode | None,
        Field(
            description="Optional currency filter, for example 'usdt'; omit for all currencies."
        ),
    ] = None,
    authorized_user: Annotated[
        bool,
        Field(
            description="Set true to request user-authorized chain data; default false uses public reference data."
        ),
    ] = False,
) -> dict[str, Any]:
    """Read HTX V2 currency and chain reference data.

    Omit ``currency`` for all currencies, or provide a code such as ``usdt`` to narrow the result. Set ``authorized_user`` only when user-specific chain visibility is needed.
    """

    return await server._public(
        "/v2/reference/currencies",
        currency=server._text(currency, "currency") if currency is not None else None,
        authorizedUser=authorized_user,
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_market_status() -> dict[str, Any]:
    """Read the current HTX spot market service status.

    Use this before interpreting a market-data gap or submitting a time-sensitive order.
    """

    return await server._public("/v2/market-status")


@server.mcp.tool(annotations=server.READ)
async def spot_get_server_timestamp() -> dict[str, Any]:
    """Read the current HTX server timestamp in milliseconds.

    Use this to compare clock skew before time-window queries or signed private requests.
    """

    return await server._public("/v1/common/timestamp")


# ---------------------------------------------------------------------------
# Spot account and read-only order inspection
# ---------------------------------------------------------------------------


@server.mcp.tool(annotations=server.READ)
async def spot_get_accounts() -> dict[str, Any]:
    """List the authenticated user's HTX account IDs and account types.

    Use the returned working ``spot`` account ID for balance and order tools when HTX_SPOT_ACCOUNT_ID is not configured.
    Requires read permission and API credentials.
    """

    return await server._private_get("/v1/account/accounts")


@server.mcp.tool(annotations=server.READ)
async def htx_diagnose_private_access() -> dict[str, Any]:
    """Diagnose authenticated access to HTX spot and USDT-swap private APIs without trading.

    The result reports credential presence, configured hosts, each check's success, and sanitized HTX error codes/messages. It never returns a secret, signature, or signed URL.
    """

    checks: dict[str, Any] = {
        "credentials_configured": server.client.credentials_configured,
        "spot_host": server.client.config.base_url,
        "futures_host": server.client.config.futures_base_url,
        "trading_enabled": server.client.config.enable_trading,
    }
    for name, path in {
        "spot_accounts": "/v1/account/accounts",
        "futures_api_trading_status": "/linear-swap-api/v1/swap_api_trading_status",
    }.items():
        try:
            response = await server._private_get(path)
            checks[name] = {
                "ok": True,
                "status": response.get("status"),
                "code": response.get("code"),
            }
        except server.HtxError as error:
            checks[name] = server._diagnostic_error(error)
    return checks


@server.mcp.tool(annotations=server.READ)
async def spot_get_account_balance(
    account_id: server.AccountId | None = None,
) -> dict[str, Any]:
    """Read balances for one authenticated HTX spot account.

    Pass ``account_id`` explicitly, or omit it to use HTX_SPOT_ACCOUNT_ID or resolve the unique working spot account automatically.
    """

    resolved = await server._resolve_spot_account_id(account_id)
    return await server._private_get(f"/v1/account/accounts/{resolved}/balance")


@server.mcp.tool(annotations=server.READ)
async def spot_get_open_orders(
    account_id: server.AccountId | None = None,
    symbol: server.SpotSymbol | None = None,
    size: Annotated[
        int,
        Field(
            ge=1,
            le=1000,
            description="Maximum open orders to return; HTX allows 1-1000.",
        ),
    ] = 100,
    direct: server.PageDirection | None = None,
    from_order_id: server.ExchangeOrderId | None = None,
) -> dict[str, Any]:
    """Read currently open HTX spot orders.

    Optionally filter by symbol and use ``direct`` plus ``from_order_id`` to paginate. This is read-only and does not cancel anything.
    """

    if not 1 <= size <= 1000:
        raise ToolError("size must be between 1 and 1000")
    if from_order_id is not None:
        server._text(from_order_id, "from_order_id")
    resolved = await server._resolve_spot_account_id(account_id)
    return await server._private_get(
        "/v1/order/openOrders",
        **{
            "account-id": resolved,
            "symbol": server._symbol(symbol) if symbol is not None else None,
            "size": size,
            "direct": direct,
            "from": from_order_id,
        },
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_order(order_id: server.ExchangeOrderId) -> dict[str, Any]:
    """Read one HTX spot order by its exchange order ID.

    The response contains the order status and fill fields; an accepted order is not necessarily filled.
    """

    return await server._private_get(
        f"/v1/order/orders/{server._text(order_id, 'order_id')}"
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_order_by_client_id(
    client_order_id: server.SpotClientOrderId,
) -> dict[str, Any]:
    """Read one HTX spot order by the server.client-generated order ID.

    Use this after a timeout when the exchange order ID was not received. Keep server.client IDs unique and reuse the same ID for reconciliation.
    """

    return await server._private_get(
        "/v1/order/orders/getClientOrder",
        clientOrderId=server._text(client_order_id, "client_order_id"),
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_match_results(
    symbol: server.SpotSymbol,
    types: Annotated[
        str | None,
        Field(
            description="Optional comma-separated spot order types to include, for example 'buy-limit,sell-limit'."
        ),
    ] = None,
    size: Annotated[
        int,
        Field(ge=1, le=500, description="Maximum fills to return; HTX allows 1-500."),
    ] = 100,
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    from_match_id: Annotated[
        str | None,
        Field(
            description="Pagination cursor: the internal match-result id, not trade-id."
        ),
    ] = None,
    direct: server.PageDirection = "next",
) -> dict[str, Any]:
    """Read filled or partially filled HTX spot match results for one symbol.

    The query window is at most 48 hours and can be shifted within the supported history period; pagination uses the internal match-result ID, not trade-id.
    """

    if not 1 <= size <= 500:
        raise ToolError("size must be between 1 and 500")
    server._validate_time_range(
        start_time,
        end_time,
        max_window_ms=48 * 60 * 60 * 1000,
        max_age_ms=180 * 24 * 60 * 60 * 1000,
    )
    if from_match_id is not None:
        server._text(from_match_id, "from_match_id")
    return await server._private_get(
        "/v1/order/matchresults",
        **{
            "symbol": server._symbol(symbol),
            "types": types,
            "size": size,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_match_id,
            "direct": direct,
        },
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_order_match_results(
    order_id: server.ExchangeOrderId,
) -> dict[str, Any]:
    """Read all HTX spot fills associated with one exchange order ID.

    Use this to confirm partial fills, fill price, fees, and filled amount after querying the order.
    """

    return await server._private_get(
        f"/v1/order/orders/{server._text(order_id, 'order_id')}/matchresults"
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_history_orders(
    symbol: server.SpotSymbol,
    states: Annotated[
        str,
        Field(
            description="Comma-separated states: submitted, partial-filled, filled, canceled, or partial-canceled."
        ),
    ] = "filled,canceled,partial-canceled",
    types: Annotated[
        str | None,
        Field(
            description="Optional comma-separated order types; omit to include all types."
        ),
    ] = None,
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    from_order_id: server.ExchangeOrderId | None = None,
    direct: server.PageDirection = "next",
    size: Annotated[
        int,
        Field(ge=1, le=100, description="Maximum orders to return; HTX allows 1-100."),
    ] = 100,
) -> dict[str, Any]:
    """Search HTX spot order history for one symbol.

    Use comma-separated ``states`` and optional order types; the query window is at most 48 hours and can be shifted within the supported history period.
    """

    if not 1 <= size <= 100:
        raise ToolError("size must be between 1 and 100")
    states = server._status_list(
        states,
        allowed={
            "submitted",
            "partial-filled",
            "filled",
            "canceled",
            "partial-canceled",
        },
    )
    server._validate_time_range(
        start_time,
        end_time,
        max_window_ms=48 * 60 * 60 * 1000,
        max_age_ms=180 * 24 * 60 * 60 * 1000,
    )
    if from_order_id is not None:
        server._text(from_order_id, "from_order_id")
    return await server._private_get(
        "/v1/order/orders",
        **{
            "symbol": server._symbol(symbol),
            "states": states,
            "types": types,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_order_id,
            "direct": direct,
            "size": size,
        },
    )


@server.mcp.tool(annotations=server.READ)
async def spot_get_recent_history_orders(
    symbol: server.SpotSymbol | None = None,
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    direct: server.PageDirection = "next",
    size: Annotated[
        int,
        Field(
            ge=10,
            le=1000,
            description="Number of orders to return; HTX allows 10-1000 for this endpoint.",
        ),
    ] = 100,
) -> dict[str, Any]:
    """Search the short-window HTX spot order-history endpoint.

    This endpoint returns at least 10 and at most 1000 records; use explicit millisecond bounds when reproducible pagination is needed.
    """

    if not 10 <= size <= 1000:
        raise ToolError("size must be between 10 and 1000")
    server._validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    return await server._private_get(
        "/v1/order/history",
        **{
            "symbol": server._symbol(symbol) if symbol is not None else None,
            "start-time": start_time,
            "end-time": end_time,
            "direct": direct,
            "size": size,
        },
    )


@server.mcp.tool(annotations=server.IDEMPOTENT_WRITE)
async def spot_dead_man_switch(
    timeout_seconds: Annotated[
        int,
        Field(
            ge=0,
            description="Timeout in seconds: 0 disables the switch; 5 or more arms it.",
        ),
    ],
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Arm or disarm HTX's spot dead-man switch.

    ``timeout_seconds=0`` disables it; a value of 5 or more arms automatic cancellation of open spot orders after the timeout. This is a state-changing tool and remains a dry run unless both confirmation gates pass.
    """

    if timeout_seconds != 0 and timeout_seconds < 5:
        raise ToolError("timeout_seconds must be 0 or at least 5")
    return await server._mutation(
        "spot_dead_man_switch",
        "/v2/algo-orders/cancel-all-after",
        {"timeout": timeout_seconds},
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_place_order(
    symbol: server.SpotSymbol,
    order_type: server.SpotOrderType,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Order amount: quote-currency value for buy-market, otherwise base-currency quantity. Pass a decimal string to preserve exact precision.",
        ),
    ],
    account_id: server.AccountId | None = None,
    price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Limit or stop-limit price; required for non-market orders.",
        ),
    ] = None,
    client_order_id: server.SpotClientOrderId | None = None,
    source: Annotated[
        Literal["spot-api"],
        Field(
            description="HTX order source for this server; keep the default 'spot-api'."
        ),
    ] = "spot-api",
    stop_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Trigger price for buy/sell-stop-limit orders; omit for other types.",
        ),
    ] = None,
    operator: server.StopOperator | None = None,
    self_match_prevent: Annotated[
        bool,
        Field(
            description="When true, ask HTX to prevent this order matching the same account's order."
        ),
    ] = False,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Place one HTX spot order, with optional client order ID, stop trigger, and self-match prevention.

    Check spot_get_symbols first for precision and minimums. Market orders omit ``price``; non-market orders require it; stop-limit orders also require ``stop_price`` and ``operator``. The result means HTX accepted the request, not that it filled. The call is a dry run unless ``confirm=true`` and HTX_ENABLE_TRADING=true.
    """

    order_type = order_type.strip().lower()
    operator = server._stop_operator(operator) if operator is not None else None
    symbol = server._symbol(symbol)
    server._validate_spot_order(order_type, amount, price, stop_price, operator)
    if client_order_id is not None:
        server._text(client_order_id, "client_order_id")
    resolved = await server._resolve_spot_account_id(account_id)
    body = server._q(
        **{
            "account-id": resolved,
            "symbol": symbol,
            "type": order_type,
            "amount": decimal_to_text(amount),
            "price": server._decimal_or_none(price),
            "source": source,
            "client-order-id": client_order_id,
            "self-match-prevent": 1 if self_match_prevent else 0,
            "stop-price": server._decimal_or_none(stop_price),
            "operator": operator,
        }
    )
    return await server._mutation(
        "spot_place_order", "/v1/order/orders/place", body, confirm
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_cancel_order(
    order_id: server.ExchangeOrderId, confirm: server.Confirm = False
) -> dict[str, Any]:
    """Request cancellation of one HTX spot order by exchange order ID.

    Cancellation is not proof that no fill occurred; query the order or its match results afterward. The call is a dry run unless both confirmation gates pass.
    """

    order_id = server._text(order_id, "order_id")
    return await server._mutation(
        "spot_cancel_order",
        f"/v1/order/orders/{order_id}/submitcancel",
        {},
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_cancel_by_client_id(
    client_order_id: server.SpotClientOrderId, confirm: server.Confirm = False
) -> dict[str, Any]:
    """Request cancellation of one HTX spot order by client order ID.

    Use the same client order ID used during placement and query the resulting order afterward. The call is a dry run unless both confirmation gates pass.
    """

    client_order_id = server._text(client_order_id, "client_order_id")
    return await server._mutation(
        "spot_cancel_by_client_id",
        "/v1/order/orders/submitcancelclientorder",
        {"client-order-id": client_order_id},
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_cancel_orders_by_ids(
    order_ids: Annotated[
        list[server.ExchangeOrderId],
        Field(
            min_length=1,
            max_length=50,
            description="1-50 HTX exchange order IDs to cancel.",
        ),
    ],
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation for 1-50 HTX spot orders by exchange order ID.

    This affects every supplied ID and is therefore destructive; use confirm=true only after reviewing the exact list. Query orders afterward to verify final states.
    """

    if not 1 <= len(order_ids) <= 50:
        raise ToolError("order_ids must contain between 1 and 50 IDs")
    normalized_ids = [
        server._text(order_id, "order_ids item") for order_id in order_ids
    ]
    return await server._mutation(
        "spot_cancel_orders_by_ids",
        "/v1/order/orders/batchcancel",
        {"order-ids": normalized_ids},
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_cancel_open_orders(
    account_id: server.AccountId | None = None,
    symbol: server.SpotSymbol | None = None,
    order_types: Annotated[
        list[server.SpotOrderType] | None,
        Field(
            description="Optional list of spot order types to cancel; omit to match all types."
        ),
    ] = None,
    side: Annotated[
        Literal["buy", "sell"] | None,
        Field(description="Optional side filter: cancel only buy or only sell orders."),
    ] = None,
    size: Annotated[
        int,
        Field(
            ge=1,
            le=100,
            description="Maximum matching open orders to cancel; HTX allows 1-100.",
        ),
    ] = 100,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of matching open HTX spot orders.

    Filters can include account, symbol, order types, and side; omitting filters can affect many orders. The call is a dry run unless both confirmation gates pass.
    """

    if not 1 <= size <= 100:
        raise ToolError("size must be between 1 and 100")
    resolved = await server._resolve_spot_account_id(account_id)
    normalized_symbols = None
    if symbol:
        normalized_symbols = ",".join(
            server._symbol(item) for item in symbol.split(",")
        )
    normalized_order_types = None
    if order_types:
        normalized_order_types = ",".join(
            server._text(item, "order_types item") for item in order_types
        )
    body = server._q(
        **{
            "account-id": resolved,
            "symbol": normalized_symbols,
            "types": normalized_order_types,
            "side": side,
            "size": size,
        }
    )
    return await server._mutation(
        "spot_cancel_open_orders",
        "/v1/order/orders/batchCancelOpenOrders",
        body,
        confirm,
    )


# ---------------------------------------------------------------------------

__all__ = [
    "spot_get_ticker",
    "spot_get_tickers",
    "spot_get_klines",
    "spot_get_depth",
    "spot_get_recent_trades",
    "spot_get_symbols",
    "spot_get_currencies",
    "spot_get_currency_reference",
    "spot_get_market_status",
    "spot_get_server_timestamp",
    "spot_get_accounts",
    "htx_diagnose_private_access",
    "spot_get_account_balance",
    "spot_get_open_orders",
    "spot_get_order",
    "spot_get_order_by_client_id",
    "spot_get_match_results",
    "spot_get_order_match_results",
    "spot_get_history_orders",
    "spot_get_recent_history_orders",
    "spot_dead_man_switch",
    "spot_place_order",
    "spot_cancel_order",
    "spot_cancel_by_client_id",
    "spot_cancel_orders_by_ids",
    "spot_cancel_open_orders",
]

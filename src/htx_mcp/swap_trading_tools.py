"""Low-level HTX tools for the swap trading API domain."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import server

# USDT-margined swap trading and strategy orders
# ---------------------------------------------------------------------------


@server.mcp.tool(annotations=server.WRITE)
async def futures_place_order(
    contract_code: server.ContractCode,
    volume: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Order volume in contract units; must be positive and respect contract precision/minimum.",
        ),
    ],
    direction: server.FuturesDirection,
    order_price_type: server.FuturesOrderPriceType = "limit",
    price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Limit/order price; required for limit, post_only, IOC, and FOK types.",
        ),
    ] = None,
    offset: server.FuturesOffset | None = None,
    lever_rate: Annotated[
        int | None,
        Field(
            gt=0,
            description="Leverage multiplier. Omit to use the account's current leverage.",
        ),
    ] = None,
    margin_mode: server.MarginMode = "isolated",
    reduce_only: Annotated[
        bool,
        Field(
            description="When true, the order may only reduce an existing position; it cannot increase exposure."
        ),
    ] = False,
    client_order_id: server.FuturesClientOrderId | None = None,
    tp_trigger_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Exchange-side take-profit trigger price; set together with tp_order_price when using a limit TP.",
        ),
    ] = None,
    tp_order_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Exchange-side take-profit execution price; requires tp_trigger_price.",
        ),
    ] = None,
    tp_order_price_type: Annotated[
        Literal["optimal_5", "optimal_10", "optimal_20"],
        Field(description="Take-profit execution depth: top 5, 10, or 20 BBO levels."),
    ] = "optimal_5",
    sl_trigger_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Exchange-side stop-loss trigger price; set together with sl_order_price when using a limit SL.",
        ),
    ] = None,
    sl_order_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Exchange-side stop-loss execution price; requires sl_trigger_price.",
        ),
    ] = None,
    sl_order_price_type: Annotated[
        Literal["optimal_5", "optimal_10", "optimal_20"],
        Field(description="Stop-loss execution depth: top 5, 10, or 20 BBO levels."),
    ] = "optimal_5",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Place one HTX USDT-margined order with optional exchange-side take-profit and stop-loss protection.

    Inspect contract rules, account state, position mode, leverage, and price limits first. In hedge mode ``offset`` is required; in one-way mode it is normally ``both``. The result means HTX accepted the order instruction, not that a position was opened or closed. This call is a dry run unless ``confirm=true`` and HTX_ENABLE_TRADING=true.
    """

    server._validate_futures_order(
        volume=volume,
        direction=direction,
        offset=offset,
        order_price_type=order_price_type,
        price=price,
        lever_rate=lever_rate,
        tp_trigger_price=tp_trigger_price,
        sl_trigger_price=sl_trigger_price,
    )
    if client_order_id is not None:
        server._positive_integer(client_order_id, "client_order_id")
    if tp_order_price is not None:
        server._positive_number(tp_order_price, "tp_order_price")
    if sl_order_price is not None:
        server._positive_number(sl_order_price, "sl_order_price")
    if tp_trigger_price is None and tp_order_price is not None:
        raise ToolError("tp_order_price requires tp_trigger_price")
    if sl_trigger_price is None and sl_order_price is not None:
        raise ToolError("sl_order_price requires sl_trigger_price")
    body = server._swap_body(
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
        tp_order_price_type=tp_order_price_type
        if tp_trigger_price is not None
        else None,
        sl_trigger_price=sl_trigger_price,
        sl_order_price=sl_order_price,
        sl_order_price_type=sl_order_price_type
        if sl_trigger_price is not None
        else None,
    )
    return await server._mutation(
        "futures_place_order",
        server._swap_endpoint("order", margin_mode),
        body,
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_place_batch_orders(
    orders: Annotated[
        list[server.FuturesBatchOrder],
        Field(
            min_length=1,
            max_length=10,
            description="1-10 order objects; each item uses the same fields as futures_place_order.",
        ),
    ],
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Place 1-10 HTX USDT-margined orders in one batch request.

    Each item uses the same required and optional fields as futures_place_order. Validate every item against contract precision and margin/position mode first; the result is an acknowledgement, not proof of fills. This call is a dry run unless both confirmation gates pass.
    """

    if not 1 <= len(orders) <= 10:
        raise ToolError("orders must contain between 1 and 10 order objects")
    normalized_orders = []
    for index, order in enumerate(orders):
        if not isinstance(order, dict):
            raise ToolError(f"orders[{index}] must be an object")
        missing = [
            key
            for key in ("contract_code", "volume", "direction", "order_price_type")
            if key not in order
        ]
        if missing:
            raise ToolError(
                f"orders[{index}] is missing required fields: {', '.join(missing)}"
            )
        if not isinstance(order["contract_code"], str):
            raise ToolError(f"orders[{index}].contract_code must be a string")
        if not isinstance(order["direction"], str):
            raise ToolError(f"orders[{index}].direction must be a string")
        if not isinstance(order["order_price_type"], str):
            raise ToolError(f"orders[{index}].order_price_type must be a string")
        if order.get("offset") is not None and not isinstance(order["offset"], str):
            raise ToolError(f"orders[{index}].offset must be a string")
        try:
            volume = server._positive_number(order["volume"], f"orders[{index}].volume")
            price = (
                server._positive_number(order["price"], f"orders[{index}].price")
                if order.get("price") is not None
                else None
            )
            lever_rate_value = order.get("lever_rate")
            if lever_rate_value is not None and (
                isinstance(lever_rate_value, bool)
                or int(lever_rate_value) != lever_rate_value
            ):
                raise ValueError("lever_rate must be an integer")
            lever_rate = int(lever_rate_value) if lever_rate_value is not None else None
            tp_trigger_price = (
                server._positive_number(
                    order["tp_trigger_price"], f"orders[{index}].tp_trigger_price"
                )
                if order.get("tp_trigger_price") is not None
                else None
            )
            sl_trigger_price = (
                server._positive_number(
                    order["sl_trigger_price"], f"orders[{index}].sl_trigger_price"
                )
                if order.get("sl_trigger_price") is not None
                else None
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ToolError(
                f"orders[{index}] contains a non-numeric order field"
            ) from exc
        server._validate_futures_order(
            volume=volume,
            direction=order["direction"],
            offset=order.get("offset"),
            order_price_type=order["order_price_type"],
            price=price,
            lever_rate=lever_rate,
            tp_trigger_price=tp_trigger_price,
            sl_trigger_price=sl_trigger_price,
        )
        try:
            tp_order_price = (
                server._positive_number(
                    order["tp_order_price"], f"orders[{index}].tp_order_price"
                )
                if order.get("tp_order_price") is not None
                else None
            )
            sl_order_price = (
                server._positive_number(
                    order["sl_order_price"], f"orders[{index}].sl_order_price"
                )
                if order.get("sl_order_price") is not None
                else None
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ToolError(
                f"orders[{index}] contains a non-numeric TP/SL price"
            ) from exc
        if tp_trigger_price is None and tp_order_price is not None:
            raise ToolError(f"orders[{index}].tp_order_price requires tp_trigger_price")
        if sl_trigger_price is None and sl_order_price is not None:
            raise ToolError(f"orders[{index}].sl_order_price requires sl_trigger_price")
        normalized_orders.append(
            {
                **order,
                "contract_code": server._contract(order["contract_code"]),
                "volume": volume,
                "reduce_only": int(bool(order.get("reduce_only", False))),
            }
        )
    body = {"orders_data": normalized_orders}
    return await server._mutation(
        "futures_place_batch_orders",
        server._swap_endpoint("batchorder", margin_mode),
        body,
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_cancel_order(
    contract_code: server.ContractCode,
    order_id: server.ExchangeOrderId | None = None,
    client_order_id: server.FuturesClientOrderId | None = None,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of one HTX USDT-margined order.

    Provide ``order_id`` or ``client_order_id``; if both are supplied HTX receives both identifiers. Query the order afterward to confirm the final status. The call is a dry run unless both confirmation gates pass.
    """

    if order_id is None and client_order_id is None:
        raise ToolError("order_id or client_order_id is required")
    if order_id is not None:
        order_id = server._text(order_id, "order_id")
    if client_order_id is not None:
        server._positive_integer(client_order_id, "client_order_id")
    body = server._swap_body(
        contract_code,
        order_id=order_id,
        client_order_id=str(client_order_id) if client_order_id is not None else None,
    )
    return await server._mutation(
        "futures_cancel_order",
        server._swap_endpoint("cancel", margin_mode),
        body,
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_cancel_all_orders(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of all open HTX USDT-margined orders for one contract.

    This is a broad destructive action for the selected margin mode and contract. Review the dry-run body before setting confirm=true, then query open orders afterward.
    """

    return await server._mutation(
        "futures_cancel_all_orders",
        server._swap_endpoint("cancelall", margin_mode),
        server._swap_body(contract_code),
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_switch_leverage(
    contract_code: server.ContractCode,
    lever_rate: Annotated[
        int,
        Field(
            gt=0,
            description="New leverage multiplier; HTX must support this value for the contract/account.",
        ),
    ],
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Change the leverage for one HTX USDT-margined contract.

    HTX may reject the change while orders or positions exist; first query available leverage and current account state. The call is a dry run unless both confirmation gates pass.
    """

    server._positive_integer(lever_rate, "lever_rate")
    return await server._mutation(
        "futures_switch_leverage",
        server._swap_endpoint("switch_lever_rate", margin_mode),
        server._swap_body(contract_code, lever_rate=lever_rate),
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_lightning_close_position(
    contract_code: server.ContractCode,
    volume: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Position volume to close in contract units; must be positive.",
        ),
    ],
    direction: server.FuturesDirection,
    margin_mode: server.MarginMode = "isolated",
    order_price_type: Annotated[
        Literal["market", "lightning_fok", "lightning_ioc"],
        Field(
            description="Close execution mode: market, lightning FOK, or lightning IOC."
        ),
    ] = "market",
    client_order_id: Annotated[
        int | None,
        Field(
            gt=0, description="Optional numeric server.client order ID for tracking."
        ),
    ] = None,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request HTX's lightning close-position order for one USDT-margined position.

    ``direction`` is the order side that closes the position (buy closes short, sell closes long). This is an aggressive execution path; query positions first and verify the result afterward. The call is a dry run unless both confirmation gates pass.
    """

    server._positive_number(volume, "volume")
    if client_order_id is not None:
        server._positive_integer(client_order_id, "client_order_id")
    if margin_mode == "isolated":
        path = "/linear-swap-api/v1/swap_lightning_close_position"
    else:
        path = "/linear-swap-api/v1/swap_cross_lightning_close_position"
    body = server._swap_body(
        contract_code,
        volume=volume,
        direction=direction,
        order_price_type=order_price_type,
        client_order_id=client_order_id,
    )
    return await server._mutation(
        "futures_lightning_close_position", path, body, confirm
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_place_trigger_order(
    contract_code: server.ContractCode,
    trigger_type: server.TriggerCondition,
    trigger_price: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Market price that activates the trigger order; must be positive.",
        ),
    ],
    volume: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Trigger order volume in contract units; must be positive.",
        ),
    ],
    direction: server.FuturesDirection,
    order_price_type: Annotated[
        Literal["limit", "optimal_5", "optimal_10", "optimal_20"],
        Field(
            description="Triggered order execution: limit requires order_price; optimal_N uses the top N BBO levels."
        ),
    ] = "limit",
    order_price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Triggered limit order price; required when order_price_type is limit.",
        ),
    ] = None,
    offset: server.FuturesOffset | None = None,
    lever_rate: Annotated[
        int | None,
        Field(gt=0, description="Optional leverage multiplier for the trigger order."),
    ] = None,
    reduce_only: Annotated[
        bool,
        Field(
            description="When true, the trigger order may only reduce an existing position."
        ),
    ] = False,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Place one HTX USDT-margined trigger order.

    ``trigger_type`` accepts readable greater_or_equal or less_or_equal values. A limit trigger requires ``order_price``; optimal_N uses the selected BBO depth. In hedge mode ``offset`` is required. The call is a dry run unless both confirmation gates pass.
    """

    trigger_type = server._trigger_condition(trigger_type)
    server._positive_number(trigger_price, "trigger_price")
    server._positive_number(volume, "volume")
    if order_price_type == "limit" and order_price is None:
        raise ToolError("order_price is required for limit trigger orders")
    if order_price is not None:
        server._positive_number(order_price, "order_price")
    if lever_rate is not None:
        server._positive_integer(lever_rate, "lever_rate")
    body = server._swap_body(
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
    return await server._mutation(
        "futures_place_trigger_order",
        server._swap_endpoint("trigger_order", margin_mode),
        body,
        confirm,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_trigger_open_orders(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
    trade_type: server.OrderTradeTypeInput = "all",
) -> dict[str, Any]:
    """Read open HTX USDT-margined trigger orders.

    Use the readable trade filters and pagination fields to inspect strategy orders without changing them.
    """

    server._page(page_index, page_size)
    trade_type = server._trade_type(trade_type, order_filter=True)
    return await server._private_post(
        server._swap_endpoint("trigger_openorders", margin_mode),
        server._swap_body(
            contract_code,
            page_index=page_index,
            page_size=page_size,
            trade_type=trade_type,
        ),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_trigger_history(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
    trade_type: server.OrderTradeTypeInput = "all",
    status: Annotated[
        str,
        Field(
            description="Comma-separated trigger status codes: 0 all, 4 submitted, 5 failed, 6 canceled."
        ),
    ] = "0",
    create_date: Annotated[
        int,
        Field(
            ge=1,
            le=90,
            description="How many recent days to search; HTX supports 1-90.",
        ),
    ] = 1,
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
    sort_by: Annotated[
        Literal["created_at", "update_time"],
        Field(description="Sort by creation time or last update time."),
    ] = "created_at",
) -> dict[str, Any]:
    """Read historical HTX USDT-margined trigger orders.

    ``create_date`` searches the most recent 1-90 days; status 0 means all, 4 submitted, 5 failed, and 6 canceled. Use pagination fields for additional pages.
    """

    server._page(page_index, page_size)
    trade_type = server._trade_type(trade_type, order_filter=True)
    status = server._status_list(status, allowed=server.TRIGGER_ORDER_STATUSES)
    server._create_date(create_date)
    return await server._private_post(
        server._swap_endpoint("trigger_hisorders", margin_mode),
        server._swap_body(
            contract_code,
            trade_type=trade_type,
            status=status,
            create_date=create_date,
            page_index=page_index,
            page_size=page_size,
            sort_by=sort_by,
        ),
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_cancel_trigger_order(
    contract_code: server.ContractCode,
    order_id: server.ExchangeOrderId,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of one HTX USDT-margined trigger order.

    Query trigger history afterward to confirm whether the exchange canceled it. The call is a dry run unless both confirmation gates pass.
    """

    order_id = server._text(order_id, "order_id")
    return await server._mutation(
        "futures_cancel_trigger_order",
        server._swap_endpoint("trigger_cancel", margin_mode),
        server._swap_body(contract_code, order_id=order_id),
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_cancel_all_trigger_orders(
    contract_code: server.ContractCode | None = None,
    direction: server.FuturesDirection | None = None,
    offset: Annotated[
        Literal["open", "close"] | None,
        Field(description="Optional trigger filter: open or close orders."),
    ] = None,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of all HTX USDT-margined trigger orders matching the supplied filters.

    Omitting filters can cancel every trigger order in the selected margin mode, so inspect the dry-run request carefully. The call is a dry run unless both confirmation gates pass.
    """

    return await server._mutation(
        "futures_cancel_all_trigger_orders",
        server._swap_endpoint("trigger_cancelall", margin_mode),
        server._swap_body(contract_code, direction=direction, offset=offset),
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_switch_position_mode(
    contract_code: server.ContractCode,
    position_mode: server.PositionMode,
    margin_mode: server.MarginMode = "isolated",
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Change one HTX USDT-margined contract between one-way and hedged position mode.

    Prefer ``one_way`` or ``hedged``; the legacy HTX values ``single_side`` and ``dual_side`` are also accepted. HTX may reject a mode change while orders or positions exist. The call is a dry run unless both confirmation gates pass.
    """

    return await server._mutation(
        "futures_switch_position_mode",
        server._swap_endpoint("switch_position_mode", margin_mode),
        server._swap_body(
            contract_code, position_mode=server._position_mode(position_mode)
        ),
        confirm,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_balance_valuation(
    valuation_asset: Annotated[
        Literal[
            "BTC",
            "USD",
            "USDT",
            "CNY",
            "EUR",
            "GBP",
            "VND",
            "HKD",
            "TWD",
            "MYR",
            "SGD",
            "KRW",
            "RUB",
            "TRY",
        ],
        Field(
            description="Asset in which HTX should report total account valuation; USDT is the default."
        ),
    ] = "USDT",
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined account valuation in a selected asset.

    The result is an exchange valuation snapshot and does not transfer or convert funds.
    """

    return await server._private_post(
        "/linear-swap-api/v1/swap_balance_valuation",
        {"valuation_asset": valuation_asset},
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_api_trading_status() -> dict[str, Any]:
    """Read the authenticated HTX swap API-trading status indicator.

    Use this to diagnose whether the API key is permitted to trade before enabling a state-changing tool.
    """

    return await server._private_get("/linear-swap-api/v1/swap_api_trading_status")


__all__ = [
    "futures_place_order",
    "futures_place_batch_orders",
    "futures_cancel_order",
    "futures_cancel_all_orders",
    "futures_switch_leverage",
    "futures_lightning_close_position",
    "futures_place_trigger_order",
    "futures_get_trigger_open_orders",
    "futures_get_trigger_history",
    "futures_cancel_trigger_order",
    "futures_cancel_all_trigger_orders",
    "futures_switch_position_mode",
    "futures_get_balance_valuation",
    "futures_get_api_trading_status",
]

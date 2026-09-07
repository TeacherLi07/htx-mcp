"""Low-level HTX tools for the swap account and history API domain."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import server

# USDT-margined swap account and order inspection
# ---------------------------------------------------------------------------


@server.mcp.tool(annotations=server.READ)
async def futures_get_account_type() -> dict[str, Any]:
    """Read the HTX USDT-swap account type.

    HTX returns account_type 1 for separate cross/isolated accounts and 2 for a
    unified account. Unified accounts do not support the legacy cross-margin
    account, position, or order endpoints.
    """

    return await server._private_get("/linear-swap-api/v3/swap_unified_account_type")


@server.mcp.tool(annotations=server.READ)
async def futures_get_account_info(
    margin_mode: server.MarginMode = "isolated",
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined account equity and margin information.

    Choose isolated or cross margin and optionally narrow the result to one contract. Requires API read permission.
    """

    return await server._private_post(
        server._swap_endpoint("account_info", margin_mode),
        server._swap_body(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_positions(
    margin_mode: server.MarginMode = "isolated",
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined positions.

    Use this before closing or reducing exposure; an empty data list means no position matched the optional contract filter.
    """

    return await server._private_post(
        server._swap_endpoint("position_info", margin_mode),
        server._swap_body(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_account_position_info(
    margin_mode: server.MarginMode = "isolated",
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read combined authenticated HTX swap assets and positions.

    This is a convenient pre-trade snapshot for account equity, margin, and open positions in the selected margin mode.
    """

    stem = "account_position_info"
    return await server._private_post(
        server._swap_endpoint(stem, margin_mode), server._swap_body(contract_code)
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_available_leverage(
    margin_mode: server.MarginMode = "isolated",
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read leverage values currently available to the authenticated HTX swap account.

    Provide ``contract_code`` when selecting leverage for one contract; use the result before futures_switch_leverage.
    """

    return await server._private_post(
        server._swap_endpoint("available_level_rate", margin_mode),
        server._swap_body(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_open_orders(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
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
    trade_type: server.OrderTradeTypeInput = "all",
) -> dict[str, Any]:
    """Read current unfilled HTX USDT-margined orders.

    Use the readable ``trade_type`` filters and page fields to inspect orders without changing them. This does not include fills that are already closed.
    """

    server._page(page_index, page_size)
    trade_type = server._trade_type(trade_type, order_filter=True)
    body = server._swap_body(
        contract_code,
        page_index=page_index,
        page_size=page_size,
        sort_by=sort_by,
        trade_type=trade_type,
    )
    return await server._private_post(
        server._swap_endpoint("openorders", margin_mode), body
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_order_info(
    contract_code: server.ContractCode,
    order_id: server.ExchangeOrderId | None = None,
    margin_mode: server.MarginMode = "isolated",
    client_order_id: server.FuturesClientOrderId | None = None,
) -> dict[str, Any]:
    """Read current status information for one HTX USDT-margined order.

    Use this after placing or canceling an order to reconcile the exchange state; acceptance is not the same as execution.
    """

    if order_id is None and client_order_id is None:
        raise ToolError("order_id or client_order_id is required")
    return await server._private_post(
        server._swap_endpoint("order_info", margin_mode),
        server._swap_body(
            contract_code,
            order_id=server._text(order_id, "order_id")
            if order_id is not None
            else None,
            client_order_id=(
                str(client_order_id) if client_order_id is not None else None
            ),
        ),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_order_detail(
    contract_code: server.ContractCode,
    order_id: server.ExchangeOrderId,
    margin_mode: server.MarginMode = "isolated",
    created_at: server.MillisecondTimestamp | None = None,
    order_type: Annotated[
        Literal[1, 2, 3, 4] | None,
        Field(
            description="HTX order type filter: 1 place, 2 cancel, 3 liquidation, 4 delivery."
        ),
    ] = None,
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
) -> dict[str, Any]:
    """Read fills and execution details for one HTX USDT-margined order.

    For recently canceled unfilled orders, HTX's retention window is longer when ``created_at`` and ``order_type`` are supplied.
    """

    server._page(page_index, page_size)
    order_id = server._text(order_id, "order_id")
    if created_at is not None:
        server._positive_integer(created_at, "created_at")
    if order_type is not None and order_type not in {1, 2, 3, 4}:
        raise ToolError("order_type must be one of 1, 2, 3, or 4")
    return await server._private_post(
        server._swap_endpoint("order_detail", margin_mode),
        server._swap_body(
            contract_code,
            order_id=order_id,
            created_at=created_at,
            order_type=order_type,
            page_index=page_index,
            page_size=page_size,
        ),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_history_orders(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
    trade_type: server.TradeTypeInput = "all",
    type: Annotated[
        Literal[1, 2],
        Field(
            description="History scope: 1 returns all orders; 2 returns only canceled orders."
        ),
    ] = 1,
    status: Annotated[
        str,
        Field(
            description="Comma-separated HTX order statuses: 0 all, 3 partial filled, 4 filled, 5 canceled, 6 partial canceled, 7 failed."
        ),
    ] = "0",
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    direct: server.PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
    size: server.V3PageSize = 20,
) -> dict[str, Any]:
    """Read historical HTX USDT-margined orders through the current v3 endpoint.

    Use readable trade filters, comma-separated status codes, and a maximum 48-hour time window; paginate with ``from_id`` from the response.
    """

    trade_type = server._trade_type(trade_type)
    status = server._status_list(status, allowed=server.FUTURES_ORDER_STATUSES)
    server._validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        server._positive_integer(from_id, "from_id")
    body = server._q(
        contract=server._contract(contract_code),
        trade_type=trade_type,
        type=type,
        status=status,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
        size=size,
    )
    return await server._private_post(
        server._swap_endpoint("hisorders", margin_mode, version="v3"), body
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_match_results(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode = "isolated",
    trade_type: server.TradeTypeInput = "all",
    pair: server.ContractCode | None = None,
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    direct: server.PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
    size: server.V3PageSize = 20,
) -> dict[str, Any]:
    """Read historical HTX USDT-margined fills through the current v3 endpoint.

    Use ``pair`` only for cross-margin queries, bound the request to at most 48 hours, and paginate with the returned query ID.
    """

    trade_type = server._trade_type(trade_type)
    if pair is not None and margin_mode != "cross":
        raise ToolError("pair is only supported for cross-margin match-result queries")
    server._validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        server._positive_integer(from_id, "from_id")
    body = server._q(
        contract=server._contract(contract_code),
        trade_type=trade_type,
        pair=server._contract(pair) if pair is not None else None,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
        size=size,
    )
    return await server._private_post(
        server._swap_endpoint("matchresults", margin_mode, version="v3"), body
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_financial_records(
    margin_mode: server.MarginMode = "isolated",
    contract_code: server.ContractCode | None = None,
    margin_account: Annotated[
        str | None,
        Field(
            description="Financial account: contract code for isolated; use 'USDT' for cross. Required if isolated has no contract_code."
        ),
    ] = None,
    transaction_types: Annotated[
        str | None,
        Field(
            description="Optional comma-separated HTX transaction type codes, such as '3,4,5'. Omit for all."
        ),
    ] = None,
    start_time: server.MillisecondTimestamp | None = None,
    end_time: server.MillisecondTimestamp | None = None,
    direct: server.PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
    size: server.V3PageSize = 20,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined financial records through the current v3 endpoint.

    For isolated mode provide ``contract_code`` or ``margin_account``; for cross mode the financial account is ``USDT``. Transaction types are comma-separated HTX codes and the time window is at most 48 hours.
    """

    if transaction_types is not None:
        transaction_types = server._status_list(
            transaction_types, allowed=server.FINANCIAL_RECORD_TYPES
        )
    server._validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        server._positive_integer(from_id, "from_id")
    contract = server._contract(contract_code) if contract_code is not None else None
    body = server._q(
        contract=contract,
        mar_acct=server._v3_margin_account(margin_mode, contract, margin_account),
        type=transaction_types,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
        size=size,
    )
    return await server._private_post(
        server._swap_endpoint("financial_record", margin_mode, version="v3"), body
    )


# ---------------------------------------------------------------------------

__all__ = [
    "futures_get_account_info",
    "futures_get_account_position_info",
    "futures_get_account_type",
    "futures_get_available_leverage",
    "futures_get_financial_records",
    "futures_get_history_orders",
    "futures_get_match_results",
    "futures_get_open_orders",
    "futures_get_order_detail",
    "futures_get_order_info",
    "futures_get_positions",
]

"""Low-level HTX tools for the swap market API domain."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import server

# USDT-margined swap market data
# ---------------------------------------------------------------------------


@server.mcp.tool(annotations=server.READ)
async def futures_get_contracts(
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read HTX USDT-margined contract metadata and trading rules.

    Omit ``contract_code`` to list all products. Use the response to check contract status, volume precision, minimum volume, price limits, and supported leverage before trading.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_contract_info",
        contract_code=server._contract(contract_code)
        if contract_code is not None
        else None,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_ticker(contract_code: server.ContractCode) -> dict[str, Any]:
    """Read the latest merged ticker for one HTX USDT-margined contract.

    Returns current price, best bid/ask, 24-hour statistics, and exchange timestamp for the supplied contract.
    """

    return await server._public(
        "/linear-swap-ex/market/detail/merged",
        contract_code=server._contract(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_tickers(
    contract_code: server.ContractCode | None = None,
    business_type: Annotated[
        Literal["swap", "futures", "all"],
        Field(
            description="Product group: swap, dated futures, or all available products."
        ),
    ] = "swap",
) -> dict[str, Any]:
    """Read merged tickers for HTX USDT-margined products.

    Use ``business_type=swap`` for perpetual swaps, ``futures`` for dated futures, or ``all`` for both; omit ``contract_code`` for a batch snapshot.
    """

    return await server._public(
        "/linear-swap-ex/market/detail/batch_merged",
        contract_code=server._contract(contract_code)
        if contract_code is not None
        else None,
        business_type=business_type,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_depth(
    contract_code: server.ContractCode,
    depth_type: server.FuturesDepthType = "step0",
) -> dict[str, Any]:
    """Read the current order-book depth for one HTX USDT-margined contract.

    ``depth_type`` selects HTX's aggregation level from step0 through step19. This is public market data.
    """

    allowed = {f"step{i}" for i in range(20)}
    if depth_type not in allowed:
        raise ToolError("depth_type must be a supported HTX step0..step19 value")
    return await server._public(
        "/linear-swap-ex/market/depth",
        contract_code=server._contract(contract_code),
        type=depth_type,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_klines(
    contract_code: server.ContractCode,
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
    """Read historical OHLCV candlesticks for one HTX USDT-margined contract.

    Time bounds use Unix milliseconds; ``size`` is 1-2000. Use futures_get_contracts first if the contract's trading state or precision is unknown.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    server._validate_time_range(from_time, to_time, max_age_ms=None)
    return await server._public(
        "/linear-swap-ex/market/history/kline",
        contract_code=server._contract(contract_code),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_recent_trades(
    contract_code: server.ContractCode,
    size: Annotated[
        int,
        Field(
            ge=1,
            le=2000,
            description="Number of recent trades to return; HTX allows 1-2000.",
        ),
    ] = 100,
) -> dict[str, Any]:
    """Read the most recent public trades for one HTX USDT-margined contract.

    ``size`` controls the number of records and may be 1-2000.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    return await server._public(
        "/linear-swap-ex/market/history/trade",
        contract_code=server._contract(contract_code),
        size=size,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_index(contract_code: server.ContractCode) -> dict[str, Any]:
    """Read the index price used by one HTX USDT-margined contract.

    Use this alongside the mark/last price when assessing liquidation or trigger risk.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_index", contract_code=server._contract(contract_code)
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_price_limit(contract_code: server.ContractCode) -> dict[str, Any]:
    """Read the current upper and lower price limits for one HTX USDT-margined contract.

    Check these limits before submitting a limit, trigger, take-profit, or stop-loss price.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_price_limit",
        contract_code=server._contract(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_open_interest(
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read current open interest for one or all HTX USDT-margined contracts.

    Omit ``contract_code`` for the public batch result.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_open_interest",
        contract_code=server._contract(contract_code)
        if contract_code is not None
        else None,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_funding_rate(
    contract_code: server.ContractCode,
) -> dict[str, Any]:
    """Read the current funding rate and next funding time for one HTX perpetual swap.

    This is market data; it does not predict funding or alter a position.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_funding_rate",
        contract_code=server._contract(contract_code),
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_batch_funding_rate(
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read current funding rates for one or all HTX USDT-margined perpetual swaps.

    Omit ``contract_code`` for a batch response across supported contracts.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_batch_funding_rate",
        contract_code=server._contract(contract_code)
        if contract_code is not None
        else None,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_historical_funding_rate(
    contract_code: server.ContractCode,
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
) -> dict[str, Any]:
    """Read paginated historical funding rates for one HTX USDT-margined contract.

    ``page_index`` is 1-based and ``page_size`` is limited to 50 by this server.
    """

    server._page(page_index, page_size)
    return await server._public(
        "/linear-swap-api/v1/swap_historical_funding_rate",
        contract_code=server._contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_api_state() -> dict[str, Any]:
    """Read the public HTX USDT-margined swap service state.

    Use this to distinguish exchange maintenance from a local connectivity or authentication failure.
    """

    return await server._public("/linear-swap-api/v1/swap_api_state")


@server.mcp.tool(annotations=server.READ)
async def futures_get_risk_info(
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read HTX swap risk information, including insurance-fund and estimated clawback data.

    Omit ``contract_code`` for the exchange-wide result, or provide one contract for a focused query.
    """

    return await server._public(
        "/linear-swap-api/v1/swap_risk_info",
        contract_code=server._contract(contract_code)
        if contract_code is not None
        else None,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_insurance_fund(
    contract_code: server.ContractCode,
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
) -> dict[str, Any]:
    """Read paginated insurance-fund records for one HTX USDT-margined contract.

    ``page_index`` is 1-based and ``page_size`` is limited to 50 by this server.
    """

    server._page(page_index, page_size)
    return await server._public(
        "/linear-swap-api/v1/swap_insurance_fund",
        contract_code=server._contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_liquidation_orders(
    contract_code: server.ContractCode | None = None,
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
    """Read public HTX liquidation-order records through the current v3 endpoint.

    Provide at least one of ``contract_code`` or ``pair`` as required by HTX; ``trade_type`` accepts readable values such as liquidate_long or liquidate_short. The time window is at most two hours and can be shifted within the supported history period.
    """

    trade_type = server._trade_type(trade_type)
    if contract_code is None and pair is None:
        raise ToolError("contract_code or pair is required")
    server._validate_time_range(start_time, end_time, max_window_ms=2 * 60 * 60 * 1000)
    if from_id is not None:
        server._positive_integer(from_id, "from_id")
    body = server._q(
        contract=server._contract(contract_code) if contract_code is not None else None,
        trade_type=trade_type,
        pair=server._contract(pair) if pair is not None else None,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
        size=size,
    )
    return await server._public_post(
        "/linear-swap-api/v3/swap_liquidation_orders", body
    )


@server.mcp.tool(annotations=server.READ)
async def futures_get_elite_ratios(
    contract_code: server.ContractCode,
    period: Annotated[
        Literal["5min", "15min", "30min", "60min", "4hour", "1day"],
        Field(description="Sentiment-ratio interval; 60min is the default."),
    ] = "60min",
) -> dict[str, Any]:
    """Read HTX top-trader account and position sentiment ratios for one contract.

    The response contains two raw HTX results, one for account ratio and one for position ratio, at the requested interval.
    """

    code = server._contract(contract_code)
    return {
        "account_ratio": await server._public(
            "/linear-swap-api/v1/swap_elite_account_ratio",
            contract_code=code,
            period=period,
        ),
        "position_ratio": await server._public(
            "/linear-swap-api/v1/swap_elite_position_ratio",
            contract_code=code,
            period=period,
        ),
    }


__all__ = [
    "futures_get_api_state",
    "futures_get_batch_funding_rate",
    "futures_get_contracts",
    "futures_get_depth",
    "futures_get_elite_ratios",
    "futures_get_funding_rate",
    "futures_get_historical_funding_rate",
    "futures_get_index",
    "futures_get_insurance_fund",
    "futures_get_klines",
    "futures_get_liquidation_orders",
    "futures_get_open_interest",
    "futures_get_price_limit",
    "futures_get_recent_trades",
    "futures_get_risk_info",
    "futures_get_ticker",
    "futures_get_tickers",
]

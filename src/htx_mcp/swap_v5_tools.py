"""HTX USDT-margined multi-asset (v5) compatibility tools.

The endpoint and field mappings mirror the v5 endpoints registered by CCXT's
HTX exchange class.  These tools are deliberately small: semantic tools use
them as the production swap path while the older linear-swap tools remain in
the opt-in ``advanced`` toolset for non-migrated accounts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import Field

from . import server


def _contract(value: str | None) -> str | None:
    return server._contract(value) if value is not None else None


async def v5_get_account_balance() -> dict[str, Any]:
    """Return the authenticated multi-asset account balance."""

    return await server._private_get("/v5/account/balance")


async def v5_get_positions(contract_code: str | None = None) -> dict[str, Any]:
    """Return open v5 positions, optionally for one contract."""

    return await server._private_get(
        "/v5/trade/position/opens", contract_code=_contract(contract_code)
    )


async def v5_get_open_orders(
    contract_code: str | None = None,
    margin_mode: Literal["isolated", "cross"] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return v5 open orders with v5 cursor-compatible filters."""

    return await server._private_get(
        "/v5/trade/order/opens",
        contract_code=_contract(contract_code),
        margin_mode=margin_mode,
        limit=limit,
    )


async def v5_get_order(
    contract_code: str,
    *,
    order_id: str | None = None,
    client_order_id: str | None = None,
    margin_mode: Literal["isolated", "cross"] | None = None,
) -> dict[str, Any]:
    """Return one v5 order; exactly one identifier is required by HTX."""

    return await server._private_get(
        "/v5/trade/order",
        contract_code=server._contract(contract_code),
        order_id=order_id,
        client_order_id=client_order_id,
        margin_mode=margin_mode,
    )


async def v5_submit_order(body: dict[str, Any], confirm: bool) -> dict[str, Any]:
    """Submit one v5 order behind the normal MCP confirmation gates."""

    return await server._mutation("v5_submit_order", "/v5/trade/order", body, confirm)


async def v5_cancel_order(body: dict[str, Any], confirm: bool) -> dict[str, Any]:
    """Cancel one v5 order behind the normal MCP confirmation gates."""

    return await server._mutation(
        "v5_cancel_order", "/v5/trade/cancel_order", body, confirm
    )


@server.mcp.tool(annotations=server.READ)
async def futures_v5_get_account_balance() -> dict[str, Any]:
    """Read the HTX v5 multi-asset USDT-margined account balance for a migrated account."""

    return await v5_get_account_balance()


@server.mcp.tool(annotations=server.READ)
async def futures_v5_get_positions(
    contract_code: server.ContractCode | None = None,
) -> dict[str, Any]:
    """Read HTX v5 USDT-margined positions, optionally restricted to one contract code."""

    return await v5_get_positions(contract_code)


@server.mcp.tool(annotations=server.READ)
async def futures_v5_get_open_orders(
    contract_code: server.ContractCode | None = None,
    margin_mode: server.MarginMode | None = None,
    limit: Annotated[
        int | None,
        Field(ge=1, le=100, description="Maximum v5 cursor page size (1-100)."),
    ] = None,
) -> dict[str, Any]:
    """Read HTX v5 unfilled orders using its cursor-based query contract and v5 margin filters."""

    return await v5_get_open_orders(contract_code, margin_mode, limit)


@server.mcp.tool(annotations=server.READ)
async def futures_v5_get_order(
    contract_code: server.ContractCode,
    order_id: server.ExchangeOrderId | None = None,
    client_order_id: Annotated[
        str | None,
        Field(
            description="Optional v5 client order identifier when exchange order ID is unavailable."
        ),
    ] = None,
    margin_mode: server.MarginMode | None = None,
) -> dict[str, Any]:
    """Read one v5 order by exchange or client order ID so an accepted mutation can be reconciled."""

    if order_id is None and client_order_id is None:
        raise ValueError("order_id or client_order_id is required")
    return await v5_get_order(
        contract_code,
        order_id=server._text(order_id, "order_id") if order_id else None,
        client_order_id=client_order_id,
        margin_mode=margin_mode,
    )


@server.mcp.tool(annotations=server.WRITE)
async def futures_v5_set_leverage(
    contract_code: server.ContractCode,
    margin_mode: server.MarginMode,
    lever_rate: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Positive v5 leverage multiplier, sent as an exact decimal string.",
        ),
    ],
    position_side: Annotated[
        Literal["long", "short", "both"] | None,
        Field(
            description="Optional v5 position side; required by HTX for some isolated hedge positions."
        ),
    ] = None,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Set v5 leverage behind the normal confirmation gates; v5 does not accept leverage on an order."""

    return await server._mutation(
        "futures_v5_set_leverage",
        "/v5/position/lever",
        server._q(
            contract_code=server._contract(contract_code),
            margin_mode=margin_mode,
            lever_rate=server._decimal_text(lever_rate),
            position_side=position_side,
        ),
        confirm,
    )


__all__ = [
    "v5_cancel_order",
    "v5_get_account_balance",
    "v5_get_open_orders",
    "v5_get_order",
    "v5_get_positions",
    "v5_submit_order",
]

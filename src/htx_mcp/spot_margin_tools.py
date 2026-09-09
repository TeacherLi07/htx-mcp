"""Low-level HTX isolated and cross spot-margin API tools."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import server
from .precision import decimal_to_text

SpotMarginMode = Annotated[
    Literal["isolated", "cross"],
    Field(
        description="Spot-margin account mode: isolated per symbol or cross across currencies."
    ),
]


def _path(mode: str, stem: str) -> str:
    return f"/v1/{'margin' if mode == 'isolated' else 'cross-margin'}/{stem}"


def _currency(value: str) -> str:
    return server._text(value, "currency").lower()


def _amount(value: Decimal) -> str:
    amount = server._positive_number(value, "amount")
    if -amount.as_tuple().exponent > 3:
        raise ToolError("spot-margin amount supports at most 3 decimal places")
    return decimal_to_text(amount)


@server.mcp.tool(annotations=server.READ)
async def spot_margin_get_account(
    margin_mode: SpotMarginMode,
    symbol: server.SpotSymbol | None = None,
) -> dict[str, Any]:
    """Read an authenticated isolated or cross spot-margin account and its risk rate.

    Isolated mode optionally accepts one symbol; cross mode does not use a symbol. The raw HTX response includes balances, loan availability, debts, interest, and liquidation fields.
    """

    if margin_mode == "cross" and symbol is not None:
        raise ToolError("symbol is only supported for isolated spot margin")
    return await server._private_get(
        _path(margin_mode, "accounts/balance"),
        symbol=server._symbol(symbol) if symbol is not None else None,
    )


@server.mcp.tool(annotations=server.READ)
async def spot_margin_get_loan_info(
    margin_mode: SpotMarginMode,
    symbol: server.SpotSymbol | None = None,
) -> dict[str, Any]:
    """Read current spot-margin interest rates, loan limits, and loanable amounts.

    Isolated mode may be narrowed to one symbol; cross mode returns currency-level limits and does not accept a symbol.
    """

    if margin_mode == "cross" and symbol is not None:
        raise ToolError("symbol is only supported for isolated spot margin")
    return await server._private_get(
        _path(margin_mode, "loan-info"),
        symbols=server._symbol(symbol) if symbol is not None else None,
    )


@server.mcp.tool(annotations=server.READ)
async def spot_margin_get_loan_orders(
    margin_mode: SpotMarginMode,
    symbol: server.SpotSymbol | None = None,
    currency: server.CurrencyCode | None = None,
    state: Annotated[
        Literal["created", "accrual", "cleared", "invalid"] | None,
        Field(description="Optional loan state filter; omit to include every state."),
    ] = None,
    size: Annotated[
        int,
        Field(
            ge=10, le=100, description="Loan records to return, from 10 through 100."
        ),
    ] = 10,
    from_loan_id: server.ExchangeOrderId | None = None,
    direct: server.PageDirection = "next",
) -> dict[str, Any]:
    """Search past isolated or cross spot-margin loan orders without changing debt.

    Use an active loan ID from this response when planning a targeted repayment. Cross margin does not accept a symbol filter.
    """

    if not 10 <= size <= 100:
        raise ToolError("size must be between 10 and 100")
    if margin_mode == "cross" and symbol is not None:
        raise ToolError("symbol is only supported for isolated spot margin")
    return await server._private_get(
        _path(margin_mode, "loan-orders"),
        symbol=server._symbol(symbol) if symbol is not None else None,
        currency=_currency(currency) if currency is not None else None,
        state=state,
        size=size,
        **{
            "from": server._text(from_loan_id, "from_loan_id")
            if from_loan_id
            else None,
            "direct": direct,
        },
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_margin_transfer(
    margin_mode: SpotMarginMode,
    direction: Annotated[
        Literal["in", "out"],
        Field(
            description="'in' moves funds from spot to margin; 'out' moves funds back to spot."
        ),
    ],
    currency: server.CurrencyCode,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Exact asset amount as a decimal string; maximum 3 decimal places.",
        ),
    ],
    symbol: server.SpotSymbol | None = None,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Transfer an asset into or out of an isolated or cross spot-margin account.

    Isolated transfers require the trading symbol; cross transfers must omit it. Pass ``confirm=true`` to request the mutation.
    """

    if margin_mode == "isolated" and symbol is None:
        raise ToolError("symbol is required for isolated spot-margin transfers")
    if margin_mode == "cross" and symbol is not None:
        raise ToolError("symbol is only supported for isolated spot margin")
    body = server._q(
        symbol=server._symbol(symbol) if symbol else None,
        currency=_currency(currency),
        amount=_amount(amount),
    )
    path = (
        f"/v1/dw/transfer-{direction}/margin"
        if margin_mode == "isolated"
        else _path(margin_mode, f"transfer-{direction}")
    )
    return await server._mutation("spot_margin_transfer", path, body, confirm)


@server.mcp.tool(annotations=server.WRITE)
async def spot_margin_borrow(
    margin_mode: SpotMarginMode,
    currency: server.CurrencyCode,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Exact asset amount to borrow as a decimal string; maximum 3 decimal places.",
        ),
    ],
    symbol: server.SpotSymbol | None = None,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Request an isolated or cross spot-margin loan after checking loan info and risk.

    Isolated loans require a trading symbol; cross loans are currency-level. An accepted loan request is not proof the funds are safely deployable; inspect the account afterward.
    """

    if margin_mode == "isolated" and symbol is None:
        raise ToolError("symbol is required for isolated spot-margin loans")
    if margin_mode == "cross" and symbol is not None:
        raise ToolError("symbol is only supported for isolated spot margin")
    body = server._q(
        symbol=server._symbol(symbol) if symbol else None,
        currency=_currency(currency),
        amount=_amount(amount),
    )
    return await server._mutation(
        "spot_margin_borrow", _path(margin_mode, "orders"), body, confirm
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_margin_repay(
    margin_mode: SpotMarginMode,
    loan_order_id: server.ExchangeOrderId,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Exact repayment amount as a decimal string; maximum 3 decimal places.",
        ),
    ],
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Repay a specific isolated or cross spot-margin loan order, including applicable interest.

    Query loan orders first and reconcile the account afterward. Pass ``confirm=true`` to request the mutation.
    """

    loan_order_id = server._text(loan_order_id, "loan_order_id")
    return await server._mutation(
        "spot_margin_repay",
        _path(margin_mode, f"orders/{loan_order_id}/repay"),
        {"amount": _amount(amount)},
        confirm,
    )


@server.mcp.tool(annotations=server.WRITE)
async def spot_margin_place_order(
    margin_mode: SpotMarginMode,
    account_id: server.AccountId,
    symbol: server.SpotSymbol,
    order_type: server.SpotOrderType,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Order amount: quote value for buy-market, otherwise base quantity; pass an exact decimal string.",
        ),
    ],
    price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Limit or stop-limit price; required for non-market orders.",
        ),
    ] = None,
    client_order_id: server.SpotClientOrderId | None = None,
    stop_price: Annotated[
        Decimal | None,
        Field(gt=0, description="Stop-limit trigger price; omit for non-stop orders."),
    ] = None,
    operator: server.StopOperator | None = None,
    confirm: server.Confirm = False,
) -> dict[str, Any]:
    """Place one isolated or cross spot-margin order using its explicit HTX margin account ID.

    Get the account first with spot_margin_get_account and use its returned ID. The tool selects HTX's margin-api or super-margin-api source; it never falls back to a normal spot account.
    """

    symbol = server._symbol(symbol)
    order_type = order_type.strip().lower()
    operator = server._stop_operator(operator) if operator is not None else None
    server._validate_spot_order(order_type, amount, price, stop_price, operator)
    if client_order_id is not None:
        server._text(client_order_id, "client_order_id")
    body = server._q(
        **{
            "account-id": server._text(account_id, "account_id"),
            "symbol": symbol,
            "type": order_type,
            "amount": decimal_to_text(amount),
            "price": server._decimal_or_none(price),
            "source": "margin-api" if margin_mode == "isolated" else "super-margin-api",
            "client-order-id": client_order_id,
            "stop-price": server._decimal_or_none(stop_price),
            "operator": operator,
        }
    )
    return await server._mutation(
        "spot_margin_place_order", "/v1/order/orders/place", body, confirm
    )


__all__ = [
    "spot_margin_borrow",
    "spot_margin_get_account",
    "spot_margin_get_loan_info",
    "spot_margin_get_loan_orders",
    "spot_margin_place_order",
    "spot_margin_repay",
    "spot_margin_transfer",
]

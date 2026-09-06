"""Intent-oriented HTX tools built on top of the complete API mapping.

This module deliberately depends on a small gateway surface from ``server``
instead of owning another HTTP client. The low-level endpoint tools remain the
compatibility layer; these tools compose them into workflows an agent can use.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from decimal import Decimal, InvalidOperation, localcontext
from types import ModuleType
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .models import (
    AccountSnapshotResult,
    Confirm,
    DecimalAmount,
    DecimalPrice,
    ExecutionResult,
    InstrumentRulesResult,
    MarginMode,
    MarketSnapshotResult,
    ReconcileTradeResult,
    RiskSnapshotResult,
    SnapshotField,
    TradeIntent,
    TradePlanResult,
    TradePreviewResult,
    TradeSubmissionResult,
    TradeValidationResult,
)
from .precision import decimal_to_text


Product = Annotated[
    Literal["spot", "swap"],
    Field(description="HTX product family: spot or USDT-margined perpetual swap."),
]
Instrument = Annotated[
    str,
    Field(
        description="Spot symbol such as 'btcusdt' or swap contract such as 'BTC-USDT'."
    ),
]
Profile = Annotated[
    Literal["minimal", "analysis", "execution"],
    Field(
        description="Snapshot size: minimal ticker, analysis context, or execution context."
    ),
]
IncludeRaw = Annotated[
    bool,
    Field(
        description="When true, include the original HTX envelopes in addition to compact normalized data."
    ),
]
SemanticClientOrderId = Annotated[
    str | int,
    Field(
        description="Product-specific client order ID: a 1-64 character identifier for spot, or a positive 64-bit integer for swaps."
    ),
]


def _data(payload: dict[str, Any]) -> Any:
    return payload.get("data", payload)


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = _data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "list", "symbols", "contracts", "orders", "positions"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
        return [data]
    return []


def _match(
    records: list[dict[str, Any]], instrument: str | None
) -> dict[str, Any] | None:
    if instrument is None:
        return None
    wanted = instrument.lower()
    for record in records:
        for key in ("symbol", "contract_code", "contract"):
            if str(record.get(key, "")).lower() == wanted:
                return record
    return None


def _number_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return decimal_to_text(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return str(value)


def _book_price(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    return _number_text(value)


def _ticker(payload: dict[str, Any]) -> dict[str, Any]:
    tick = payload.get("tick")
    if not isinstance(tick, dict):
        value = _data(payload)
        tick = value if isinstance(value, dict) else {}
    return {
        "last": _number_text(tick.get("close") or tick.get("last_price")),
        "bid": _book_price(tick.get("bid") or tick.get("bid_price")),
        "ask": _book_price(tick.get("ask") or tick.get("ask_price")),
        "open": _number_text(tick.get("open")),
        "high": _number_text(tick.get("high")),
        "low": _number_text(tick.get("low")),
        "volume": _number_text(tick.get("vol") or tick.get("amount")),
        "timestamp_ms": payload.get("ts"),
    }


def _depth(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tick") or _data(payload)
    value = value if isinstance(value, dict) else {}
    return {
        "bids": value.get("bids", [])[:20],
        "asks": value.get("asks", [])[:20],
        "levels_returned": min(
            20, len(value.get("bids", [])), len(value.get("asks", []))
        ),
        "timestamp_ms": payload.get("ts"),
    }


def _rule_decimal(record: dict[str, Any] | None, *keys: str) -> Decimal | None:
    if not record:
        return None
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            try:
                return Decimal(str(value))
            except (InvalidOperation, ValueError):
                pass
    return None


def _wire(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_text(value)
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_wire(item) for item in value]
    return value


def _client_order_id(product: str, value: str | int | None) -> str | int | None:
    """Validate product-specific order IDs used outside TradeIntent."""

    if value is None:
        return None
    if product == "spot":
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", value
        ):
            raise ToolError(
                "spot client_order_id must contain 1-64 letters, digits, underscores, or hyphens"
            )
        return value
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 9223372036854775807
    ):
        raise ToolError(
            "swap client_order_id must be an integer from 1 through 9223372036854775807"
        )
    return value


def _check(
    checks: list[dict[str, str]], severity: str, code: str, message: str
) -> None:
    checks.append({"severity": severity, "code": code, "message": message})


def _step_check(
    checks: list[dict[str, str]], value: Decimal | None, step: Decimal | None, name: str
) -> None:
    if value is None or step is None or step <= 0:
        return
    with localcontext() as context:
        context.prec = max(
            50,
            len(value.as_tuple().digits) + 10,
            len(step.as_tuple().digits) + 10,
        )
        if value % step != 0:
            _check(
                checks,
                "error",
                f"{name}_precision",
                f"{name}={decimal_to_text(value)} is not aligned to HTX step {decimal_to_text(step)}.",
            )


def _precision_check(
    checks: list[dict[str, str]],
    value: Decimal | None,
    rule_value: Decimal | None,
    name: str,
) -> None:
    """Handle both HTX decimal-place fields and explicit tick-size fields."""

    if value is None or rule_value is None or rule_value <= 0:
        return
    if rule_value == rule_value.to_integral_value() and rule_value >= 1:
        max_places = int(rule_value)
        places = max(0, -value.as_tuple().exponent)
        if places > max_places:
            _check(
                checks,
                "error",
                f"{name}_precision",
                f"{name} has {places} decimal places but HTX allows at most {max_places}.",
            )
        return
    _step_check(checks, value, rule_value, name)


def _market_fields(product: str, profile: str, include: list[str] | None) -> set[str]:
    if include:
        return set(include)
    if profile == "minimal":
        return {"ticker"}
    if profile == "execution":
        return (
            {"ticker", "depth", "price_limit"}
            if product == "swap"
            else {"ticker", "depth"}
        )
    return (
        {
            "ticker",
            "depth",
            "klines",
            "index",
            "funding",
            "open_interest",
            "price_limit",
        }
        if product == "swap"
        else {"ticker", "depth", "klines"}
    )


def register_semantic_tools(mcp: Any, api: ModuleType) -> None:
    """Register the semantic facade against the existing raw server gateway."""

    async def fetch_rules(product: str, instrument: str | None) -> dict[str, Any]:
        if product == "spot":
            code = api._symbol(instrument) if instrument else None
            payload = await api.spot_get_symbols()
        else:
            code = api._contract(instrument) if instrument else None
            payload = await api.futures_get_contracts(code)
        records = _records(payload)
        matched = _match(records, code)
        return {
            "product": product,
            "instrument": code,
            "matched": matched,
            "rules": matched if code else records,
            "available_count": len(records),
        }

    async def fetch_market(
        product: str,
        instrument: str,
        profile: str,
        include: list[str] | None,
        period: str,
        candle_size: int,
        include_raw: bool,
    ) -> dict[str, Any]:
        code = (
            api._symbol(instrument) if product == "spot" else api._contract(instrument)
        )
        fields = _market_fields(product, profile, include)
        calls: dict[str, Any] = {}
        if "ticker" in fields:
            calls["ticker"] = (
                api.spot_get_ticker(code)
                if product == "spot"
                else api.futures_get_ticker(code)
            )
        if "depth" in fields:
            calls["depth"] = (
                api.spot_get_depth(code)
                if product == "spot"
                else api.futures_get_depth(code)
            )
        if "klines" in fields:
            calls["klines"] = (
                api.spot_get_klines(code, period=period, size=candle_size)
                if product == "spot"
                else api.futures_get_klines(code, period=period, size=candle_size)
            )
        warnings: list[str] = []
        if "contracts" in fields:
            if product == "swap":
                calls["contracts"] = api.futures_get_contracts(code)
            else:
                warnings.append("contracts: field is only supported for swap snapshots")
        if product == "swap":
            optional = {
                "index": api.futures_get_index,
                "funding": api.futures_get_funding_rate,
                "open_interest": api.futures_get_open_interest,
                "price_limit": api.futures_get_price_limit,
            }
            for name, function in optional.items():
                if name in fields:
                    calls[name] = function(code)
        results = await asyncio.gather(*calls.values(), return_exceptions=True)
        data: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        for name, result in zip(calls, results):
            if isinstance(result, Exception):
                warnings.append(f"{name}: {type(result).__name__}: {result}")
                continue
            raw[name] = result
            data[name] = (
                _ticker(result)
                if name == "ticker"
                else _depth(result)
                if name == "depth"
                else _data(result)
            )
        response: dict[str, Any] = {
            "product": product,
            "instrument": code,
            "profile": profile,
            "as_of_ms": int(time.time() * 1000),
            "data": data,
            "warnings": warnings,
        }
        if include_raw:
            response["raw"] = raw
        return response

    async def fetch_account(
        product: str,
        instrument: str | None,
        margin_mode: str,
        include: list[str] | None,
        include_raw: bool,
    ) -> dict[str, Any]:
        fields = set(
            include
            or (
                ["balances", "open_orders"]
                if product == "spot"
                else ["balances", "positions", "open_orders", "api_status"]
            )
        )
        result: dict[str, Any] = {
            "product": product,
            "instrument": instrument,
            "margin_mode": margin_mode if product == "swap" else None,
            "as_of_ms": int(time.time() * 1000),
            "warnings": [],
        }
        raw: dict[str, Any] = {}
        if product == "spot":
            unsupported = fields.intersection({"positions", "api_status"})
            result["warnings"].extend(
                f"{field}: field is only supported for swap account snapshots"
                for field in sorted(unsupported)
            )
            account_id = await api._resolve_spot_account_id(None)
            result["account_id"] = account_id
            if "balances" in fields:
                raw["balances"] = await api.spot_get_account_balance(account_id)
                result["balances"] = _data(raw["balances"])
            if "open_orders" in fields:
                raw["open_orders"] = await api.spot_get_open_orders(
                    account_id, api._symbol(instrument) if instrument else None
                )
                result["open_orders"] = _data(raw["open_orders"])
        else:
            code = api._contract(instrument) if instrument else None
            if "balances" in fields:
                raw["balances"] = await api.futures_get_account_info(margin_mode, code)
                result["balances"] = _data(raw["balances"])
            if "positions" in fields:
                raw["positions"] = await api.futures_get_positions(margin_mode, code)
                result["positions"] = _data(raw["positions"])
            if "open_orders" in fields and code:
                raw["open_orders"] = await api.futures_get_open_orders(
                    code, margin_mode
                )
                result["open_orders"] = _data(raw["open_orders"])
            elif "open_orders" in fields:
                result["warnings"].append(
                    "open_orders: instrument is required for swap account snapshots"
                )
            if "api_status" in fields:
                raw["api_status"] = await api.futures_get_api_trading_status()
                result["api_status"] = _data(raw["api_status"])
        if include_raw:
            result["raw"] = raw
        return result

    def validation_summary(result: TradePlanResult) -> TradeValidationResult:
        return {
            "plan_id": result["plan_id"],
            "status": result["status"],
            "product": result["product"],
            "instrument": result["instrument"],
            "reference_price": result["reference_price"],
            "rules": result["rules"],
            "checks": result["checks"],
            "revalidate_before_execution": result["revalidate_before_execution"],
        }

    def order_price_type(product: str, side: str, kind: str) -> str:
        if product == "spot":
            if kind == "market":
                return f"{side}-market"
            if kind == "post_only":
                return f"{side}-limit-maker"
            if kind == "fok":
                return f"{side}-limit-fok"
            return f"{side}-{kind}"
        return {
            "market": "lightning",
            "limit": "limit",
            "post_only": "post_only",
            "ioc": "ioc",
            "fok": "fok",
        }[kind]

    async def build_request(
        intent: TradeIntent, resolve_account: bool
    ) -> dict[str, Any]:
        if intent.product == "spot":
            symbol = api._symbol(intent.instrument)
            account_id = intent.account_id or api.client.config.spot_account_id
            if not account_id and resolve_account:
                account_id = await api._resolve_spot_account_id(None)
            return api._q(
                **{
                    "account-id": account_id or "<auto-resolve>",
                    "symbol": symbol,
                    "type": order_price_type("spot", intent.side, intent.order_kind),
                    "amount": decimal_to_text(intent.quantity),
                    "price": decimal_to_text(intent.price)
                    if intent.order_kind != "market" and intent.price is not None
                    else None,
                    "source": "spot-api",
                    "client-order-id": intent.client_order_id,
                }
            )
        contract = api._contract(intent.instrument)
        body = api._q(
            contract_code=contract,
            volume=decimal_to_text(intent.quantity),
            direction=intent.side,
            offset="close" if intent.action in {"close", "reduce"} else "open",
            lever_rate=intent.leverage,
            price=(
                decimal_to_text(intent.price)
                if intent.order_kind != "market" and intent.price is not None
                else None
            ),
            order_price_type=order_price_type("swap", intent.side, intent.order_kind),
            reduce_only=1 if intent.reduce_only or intent.action == "reduce" else 0,
            client_order_id=intent.client_order_id,
            tp_trigger_price=decimal_to_text(intent.take_profit.trigger_price)
            if intent.take_profit
            else None,
            tp_order_price=decimal_to_text(intent.take_profit.order_price)
            if intent.take_profit and intent.take_profit.order_price
            else None,
            tp_order_price_type=intent.take_profit.order_price_type
            if intent.take_profit
            else None,
            sl_trigger_price=decimal_to_text(intent.stop_loss.trigger_price)
            if intent.stop_loss
            else None,
            sl_order_price=decimal_to_text(intent.stop_loss.order_price)
            if intent.stop_loss and intent.stop_loss.order_price
            else None,
            sl_order_price_type=intent.stop_loss.order_price_type
            if intent.stop_loss
            else None,
        )
        return body

    async def validate(intent: TradeIntent) -> TradePlanResult:
        instrument = (
            api._symbol(intent.instrument)
            if intent.product == "spot"
            else api._contract(intent.instrument)
        )
        checks: list[dict[str, str]] = []
        if intent.product == "spot" and intent.action != "open":
            _check(
                checks,
                "error",
                "spot_action",
                "Spot trade intents only support action='open'.",
            )
        if intent.product == "spot" and (intent.take_profit or intent.stop_loss):
            _check(
                checks,
                "error",
                "spot_protection",
                "Use swap protection fields for take-profit/stop-loss; spot protection needs a separate stop order.",
            )
        if intent.order_kind == "market" and intent.price is not None:
            _check(
                checks,
                "warning",
                "market_price_ignored",
                "Market order price will be ignored.",
            )
        if intent.order_kind != "market" and intent.price is None:
            _check(
                checks,
                "error",
                "missing_price",
                "A non-market order requires an exact decimal price.",
            )
        rules = await fetch_rules(intent.product, instrument)
        rule = rules.get("matched")
        if rule is None:
            _check(
                checks,
                "error",
                "instrument_not_found",
                f"HTX did not return rules for {instrument}.",
            )
        else:
            if intent.product == "swap":
                volume_tick = _rule_decimal(rule, "volume_tick")
                _step_check(checks, intent.quantity, volume_tick, "quantity")
                volume_precision = _rule_decimal(
                    rule, "volume-precision", "volume_precision"
                )
                _precision_check(checks, intent.quantity, volume_precision, "quantity")
            else:
                quantity_precision = _rule_decimal(
                    rule,
                    "value-precision"
                    if intent.side == "buy" and intent.order_kind == "market"
                    else "amount-precision",
                    "value_precision"
                    if intent.side == "buy" and intent.order_kind == "market"
                    else "amount_precision",
                )
                _precision_check(
                    checks, intent.quantity, quantity_precision, "quantity"
                )
            if intent.product == "spot" and intent.order_kind == "market":
                minimum = (
                    _rule_decimal(rule, "min-order-value", "min_order_value")
                    if intent.side == "buy"
                    else _rule_decimal(
                        rule,
                        "sell-market-min-order-amt",
                        "sell_market_min_order_amt",
                        "min-order-amt",
                        "min_order_amt",
                    )
                )
            else:
                minimum = _rule_decimal(
                    rule,
                    "limit-order-min-order-amt",
                    "limit_order_min_order_amt",
                    "min-order-amt",
                    "min_order_amt",
                    "min_volume",
                    "min-volume",
                )
            if minimum is not None and intent.quantity < minimum:
                _check(
                    checks,
                    "error",
                    "order_value_below_minimum"
                    if intent.product == "spot"
                    and intent.side == "buy"
                    and intent.order_kind == "market"
                    else "quantity_below_minimum",
                    (
                        "Quote-currency order value"
                        if intent.product == "spot"
                        and intent.side == "buy"
                        and intent.order_kind == "market"
                        else "Quantity"
                    )
                    + f" is below HTX minimum {decimal_to_text(minimum)}.",
                )
            if intent.order_kind != "market":
                price_tick = _rule_decimal(rule, "price-tick", "price_tick")
                _step_check(checks, intent.price, price_tick, "price")
                if intent.product == "spot" and intent.price is not None:
                    minimum_value = _rule_decimal(
                        rule, "min-order-value", "min_order_value"
                    )
                    order_value = intent.quantity * intent.price
                    if minimum_value is not None and order_value < minimum_value:
                        _check(
                            checks,
                            "error",
                            "order_value_below_minimum",
                            f"Order value {decimal_to_text(order_value)} is below HTX minimum {decimal_to_text(minimum_value)}.",
                        )
        market = await fetch_market(
            intent.product, instrument, "execution", None, "1hour", 1, False
        )
        reference = None
        try:
            reference = Decimal(
                str(market.get("data", {}).get("ticker", {}).get("last"))
            )
        except (InvalidOperation, TypeError, ValueError):
            pass
        entry = (
            intent.price if intent.order_kind != "market" else reference
        ) or reference
        if intent.product == "swap" and entry is not None:
            if intent.take_profit:
                valid = (
                    intent.take_profit.trigger_price > entry
                    if intent.side == "buy"
                    else intent.take_profit.trigger_price < entry
                )
                if not valid:
                    _check(
                        checks,
                        "error",
                        "take_profit_direction",
                        "Take-profit trigger is on the wrong side of entry price.",
                    )
            if intent.stop_loss:
                valid = (
                    intent.stop_loss.trigger_price < entry
                    if intent.side == "buy"
                    else intent.stop_loss.trigger_price > entry
                )
                if not valid:
                    _check(
                        checks,
                        "error",
                        "stop_loss_direction",
                        "Stop-loss trigger is on the wrong side of entry price.",
                    )
        request = await build_request(intent, False)
        plan_input = _wire(
            {"intent": intent.model_dump(mode="json"), "request": request}
        )
        plan_id = hashlib.sha256(
            json.dumps(plan_input, sort_keys=True).encode()
        ).hexdigest()[:16]
        return {
            "plan_id": plan_id,
            "status": "blocked"
            if any(item["severity"] == "error" for item in checks)
            else "ready",
            "product": intent.product,
            "instrument": instrument,
            "reference_price": decimal_to_text(reference)
            if reference is not None
            else None,
            "rules": rule,
            "checks": checks,
            "request": request,
            "market": market,
            "revalidate_before_execution": True,
        }

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_market_snapshot(
        product: Product,
        instrument: Instrument,
        profile: Profile = "analysis",
        include: Annotated[
            list[SnapshotField] | None,
            Field(
                description="Optional fields to include; omit to use the selected profile."
            ),
        ] = None,
        period: Annotated[
            str,
            Field(
                description="Candle period when klines are included, for example '1hour'."
            ),
        ] = "1hour",
        candle_size: Annotated[
            int,
            Field(
                ge=1, le=2000, description="Number of candles when klines are included."
            ),
        ] = 100,
        include_raw: IncludeRaw = False,
    ) -> MarketSnapshotResult:
        """Return one compact, concurrently collected market snapshot for analysis or execution.

        Prefer this aggregate over several low-level market calls.
        """

        return await fetch_market(
            product, instrument, profile, include, period, candle_size, include_raw
        )

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_instrument_rules(
        product: Product,
        instrument: Annotated[
            Instrument | None,
            Field(
                description="Optional exact symbol or contract; omit to list all rules."
            ),
        ] = None,
    ) -> InstrumentRulesResult:
        """Return HTX precision, minimum, status, and contract rules for one product.

        Use these rules before constructing any quantity or price.
        """

        return await fetch_rules(product, instrument)

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_account_snapshot(
        product: Product,
        instrument: Annotated[
            Instrument | None,
            Field(
                description="Optional exact symbol or contract to narrow positions/orders."
            ),
        ] = None,
        margin_mode: MarginMode = "isolated",
        include: Annotated[
            list[Literal["balances", "positions", "open_orders", "api_status"]] | None,
            Field(
                description="Account sections to include. Omit for spot balances/open orders or swap balances/positions/open orders/API status. Spot does not support positions or API status; swap open orders require instrument."
            ),
        ] = None,
        include_raw: IncludeRaw = False,
    ) -> AccountSnapshotResult:
        """Return a compact authenticated snapshot of balances, positions, and orders.

        This is read-only and requires API credentials.
        """

        return await fetch_account(
            product, instrument, margin_mode, include, include_raw
        )

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_risk_snapshot(
        product: Product,
        instrument: Instrument,
        margin_mode: MarginMode = "isolated",
    ) -> RiskSnapshotResult:
        """Combine current market and account state into a pre-trade risk snapshot.

        The tool supplies facts and warnings; it does not make a trading decision.
        """

        market, account = await asyncio.gather(
            fetch_market(product, instrument, "execution", None, "1hour", 1, False),
            fetch_account(product, instrument, margin_mode, None, False),
            return_exceptions=True,
        )
        warnings: list[str] = []
        if isinstance(market, Exception):
            warnings.append(f"market: {market}")
            market = {"warnings": [str(market)]}
        if isinstance(account, Exception):
            warnings.append(f"account: {account}")
            account = {"warnings": [str(account)]}
        return {
            "product": product,
            "instrument": instrument,
            "market": market,
            "account": account,
            "warnings": warnings,
        }

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_validate_trade_intent(intent: TradeIntent) -> TradeValidationResult:
        """Validate a product-neutral trade intent and return checks without a request preview."""

        return validation_summary(await validate(intent))

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_preview_trade(intent: TradeIntent) -> TradePreviewResult:
        """Build a normalized dry-run request after validating a product-neutral trade intent."""

        result = await validate(intent)
        result["execution"] = {"executed": False, "dry_run": True}
        return result

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_submit_trade(
        intent: TradeIntent, confirm: Confirm = False
    ) -> TradeSubmissionResult:
        """Revalidate and submit one normalized spot or swap trade intent behind both safety gates."""

        validation = await validate(intent)
        if validation["status"] == "blocked":
            execution: ExecutionResult = {
                "executed": False,
                "dry_run": True,
                "reason": "validation_blocked",
            }
            return {"validation": validation, "execution": execution}
        request = await build_request(
            intent,
            resolve_account=(
                confirm
                and api.client.config.enable_trading
                and api.client.credentials_configured
            ),
        )
        path = (
            "/v1/order/orders/place"
            if intent.product == "spot"
            else api._swap_endpoint("order", intent.margin_mode)
        )
        try:
            execution = await api._mutation("htx_submit_trade", path, request, confirm)
        except api.HtxError as exc:
            execution = {
                "executed": False,
                "dry_run": False,
                "ok": False,
                "error": api._diagnostic_error(exc),
            }
        return {"validation": validation, "execution": execution}

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_reconcile_trade(
        product: Product,
        instrument: Instrument,
        order_id: Annotated[
            str | None,
            Field(
                description="HTX exchange order ID; provide this or client_order_id."
            ),
        ] = None,
        client_order_id: Annotated[
            SemanticClientOrderId | None,
            Field(
                description="Client order ID used at submission; provide this or order_id."
            ),
        ] = None,
        margin_mode: MarginMode = "isolated",
    ) -> ReconcileTradeResult:
        """Read final order and fill state after a submission or network timeout.

        Query this before retrying a timed-out submission.
        """

        if not order_id and not client_order_id:
            raise ToolError("order_id or client_order_id is required")
        client_order_id = _client_order_id(product, client_order_id)
        if product == "spot":
            if client_order_id and not order_id:
                payload = await api.spot_get_order_by_client_id(client_order_id)
            else:
                payload = await api.spot_get_order(order_id or "")
        else:
            payload = await api.futures_get_order_info(
                api._contract(instrument),
                order_id=order_id,
                margin_mode=margin_mode,
                client_order_id=client_order_id,
            )
        return {"product": product, "instrument": instrument, "order": _data(payload)}

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_cancel_trade(
        product: Product,
        instrument: Instrument,
        order_id: Annotated[
            str | None,
            Field(
                description="HTX exchange order ID; provide this or client_order_id."
            ),
        ] = None,
        client_order_id: Annotated[
            SemanticClientOrderId | None,
            Field(
                description="Client order ID used at submission; provide this or order_id."
            ),
        ] = None,
        margin_mode: MarginMode = "isolated",
        confirm: Confirm = False,
    ) -> ExecutionResult:
        """Request cancellation of one normalized spot or swap order.

        Cancellation can race with fills; call htx_reconcile_trade afterward to verify the final order state.
        """

        if not order_id and not client_order_id:
            raise ToolError("order_id or client_order_id is required")
        client_order_id = _client_order_id(product, client_order_id)
        if product == "spot":
            if client_order_id and not order_id:
                path = "/v1/order/orders/submitcancelclientorder"
                body = {"client-order-id": client_order_id}
            else:
                path = (
                    f"/v1/order/orders/{api._text(order_id, 'order_id')}/submitcancel"
                )
                body = {}
        else:
            path = api._swap_endpoint("cancel", margin_mode)
            body = api._swap_body(
                api._contract(instrument),
                order_id=order_id,
                client_order_id=str(client_order_id)
                if client_order_id is not None
                else None,
            )
        return await api._mutation("htx_cancel_trade", path, body, confirm)

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_close_position(
        instrument: Annotated[
            str, Field(description="USDT-swap contract code such as 'BTC-USDT'.")
        ],
        quantity: DecimalAmount,
        side: Annotated[
            Literal["buy", "sell"],
            Field(description="Buy closes a short; sell closes a long."),
        ],
        price: DecimalPrice | None = None,
        margin_mode: MarginMode = "isolated",
        order_kind: Annotated[
            Literal["market", "ioc", "fok"],
            Field(
                description="Close execution style; market is the default aggressive mode."
            ),
        ] = "market",
        confirm: Confirm = False,
    ) -> TradeSubmissionResult:
        """Close a USDT-swap position through the validated execution path.

        The default is a dry run and the final order must be reconciled.
        """

        intent = TradeIntent(
            product="swap",
            instrument=instrument,
            action="close",
            side=side,
            order_kind=order_kind,
            quantity=quantity,
            price=price,
            margin_mode=margin_mode,
            reduce_only=True,
        )
        return await htx_submit_trade(intent, confirm)

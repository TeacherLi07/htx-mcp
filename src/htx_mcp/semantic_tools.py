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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from types import ModuleType
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, model_validator

from .client import HtxApiError
from .indicators import (
    calculate_indicator_values,
    calculate_indicators,
    candles_from_htx,
    canonical_indicator_spec,
    format_indicator_value,
    indicator_components,
    period_ms,
    price_decimal_places,
)
from .models import (
    AccountSnapshotResult,
    BatchTradeSubmissionResult,
    Confirm,
    DecimalAmount,
    DecimalPrice,
    ExecutionResult,
    InstrumentRulesResult,
    MarginMode,
    MarketSnapshotResult,
    MarketWaitResult,
    ReconcileTradeResult,
    RiskSnapshotResult,
    SnapshotField,
    SpotMarginAction,
    SpotMarginExecutionResult,
    SpotMarginPlanResult,
    TechnicalIndicatorsResult,
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


class MarketWaitCondition(BaseModel):
    """One declarative, read-only condition evaluated by the market wait tool."""

    metric: Literal["last_price", "indicator"]
    operator: Literal["gte", "lte"]
    value: Decimal
    product: Literal["spot", "swap"] | None = None
    instrument: str | None = None
    indicator: str | None = None
    component: str = "value"

    @model_validator(mode="after")
    def validate_indicator(self) -> MarketWaitCondition:
        if self.metric == "indicator" and not self.indicator:
            raise ValueError("indicator is required when metric is 'indicator'")
        if self.metric == "last_price" and self.indicator is not None:
            raise ValueError("indicator is only valid when metric is 'indicator'")
        if self.value <= 0 and self.metric == "last_price":
            raise ValueError("last_price threshold must be positive")
        if self.instrument is not None and not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        return self


IncludeRaw = Annotated[
    bool,
    Field(
        description="When true, include the original HTX envelopes in addition to compact normalized data."
    ),
]
SemanticClientOrderId = Annotated[
    str | int,
    Field(
        description="Client order ID: spot accepts a 1-64 character identifier; HTX V5 and legacy swaps require a decimal positive 64-bit integer."
    ),
]


def _data(payload: dict[str, Any]) -> Any:
    return payload.get("data", payload)


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = _data(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in (
            "items",
            "list",
            "symbols",
            "contracts",
            "orders",
            "positions",
            "trades",
            "matches",
        ):
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


def _client_order_id(product: str, value: str | int | None) -> str | None:
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
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ToolError(
            "swap client_order_id must be a decimal integer from 1 through 9223372036854775807"
        )
    text = str(value)
    if not re.fullmatch(r"[0-9]+", text) or not 1 <= int(text) <= 9223372036854775807:
        raise ToolError(
            "swap client_order_id must be a decimal integer from 1 through 9223372036854775807"
        )
    return str(int(text))


def _swap_client_order_id_text(value: str | int | None) -> str | None:
    """Canonicalize valid swap IDs while allowing a blocked preview to show input."""

    if value is None:
        return None
    text = str(value)
    if re.fullmatch(r"[0-9]+", text) and 1 <= int(text) <= 9223372036854775807:
        return str(int(text))
    return text


def _normalized_order_id(record: Any) -> Any:
    """Preserve the semantic order ID across HTX terminal-state variants."""

    if isinstance(record, list):
        return [_normalized_order_id(item) for item in record]
    if not isinstance(record, dict):
        return record
    normalized = dict(record)
    order_id = _first_value(normalized, "id", "order_id", "order_id_str", "order-id")
    if order_id is not None:
        normalized["id"] = str(order_id)
    return normalized


def _read_error(api: ModuleType, error: Exception) -> dict[str, Any]:
    """Return a sanitized, retry-classified diagnostic for semantic read tools."""

    report = api._diagnostic_error(error)
    status = report.get("http_status")
    report["retryable"] = isinstance(error, asyncio.TimeoutError) or (
        isinstance(status, int) and (status == 408 or status == 429 or status >= 500)
    )
    return report


def _is_zero_balance(record: dict[str, Any]) -> bool:
    """Whether an HTX spot balance record has explicit numeric values all at zero."""

    values = [
        record[key]
        for key in ("balance", "available", "frozen", "debt")
        if key in record and record[key] not in (None, "")
    ]
    if not values:
        return False
    try:
        return all(Decimal(str(value)) == 0 for value in values)
    except InvalidOperation:
        return False


def _compact_spot_balances(balances: Any) -> tuple[Any, int]:
    if not isinstance(balances, dict) or not isinstance(balances.get("list"), list):
        return balances, 0
    kept = [
        item
        for item in balances["list"]
        if not isinstance(item, dict) or not _is_zero_balance(item)
    ]
    return {**balances, "list": kept}, len(balances["list"]) - len(kept)


def _display_decimal(
    value: Decimal, *, significant_digits: int | None = None, places: int | None = None
) -> str:
    """Format a display value without changing the Decimal used for decisions."""

    if places is not None:
        rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    elif value.is_zero():
        rounded = Decimal(0)
    else:
        assert significant_digits is not None
        exponent = value.copy_abs().adjusted() - significant_digits + 1
        rounded = value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_UP)
    text = decimal_to_text(Decimal(0) if rounded.is_zero() else rounded)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _spot_balance_amount(record: dict[str, Any]) -> Decimal | None:
    value = record.get("balance")
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _normalize_v5_balances(balances: Any, *, verbose: bool) -> Any:
    """Expose verified per-asset V5 margin totals without trusting zero envelopes."""

    if not isinstance(balances, dict):
        return balances
    assets = balances.get("assets")
    if not isinstance(assets, list):
        return balances
    details = {
        str(asset.get("currency")).upper(): asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("currency")
    }
    primary = details.get("USDT") or next(iter(details.values()), None)
    if not isinstance(primary, dict):
        return balances
    numeric_fields = ("equity", "available_margin", "available", "margin_balance")
    if not any(primary.get(field) not in (None, "") for field in numeric_fields):
        return balances
    normalized: dict[str, Any] = {
        "primary_margin_asset": str(primary["currency"]).upper(),
    }
    for field in numeric_fields:
        if primary.get(field) not in (None, ""):
            normalized[field] = _number_text(primary[field])
    if verbose:
        return {**balances, "details": details, **normalized}
    return normalized


def _first_value(record: dict[str, Any], *keys: str) -> Any:
    """Return the first present non-empty field across HTX response variants."""

    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def _normalized_trade(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize spot, legacy-swap, and v5 execution fields for analysis tools."""

    side = _first_value(record, "side", "direction", "type")
    if isinstance(side, str) and "-" in side:
        side = side.split("-", 1)[0]
    return {
        "trade_id": _first_value(record, "trade_id", "match-id", "match_id", "id"),
        "order_id": _first_value(record, "order_id", "order-id"),
        "client_order_id": _first_value(record, "client_order_id", "client-order-id"),
        "instrument": _first_value(record, "contract_code", "symbol", "contract"),
        "side": side,
        "price": _number_text(
            _first_value(record, "trade_price", "price", "filled-price", "filled_price")
        ),
        "quantity": _number_text(
            _first_value(
                record,
                "trade_volume",
                "filled_amount",
                "filled-amount",
                "volume",
                "filled_quantity",
            )
        ),
        "fee": _number_text(
            _first_value(record, "transact_fee", "filled-fees", "fee", "trade_fee")
        ),
        "fee_currency": _first_value(
            record, "fee_currency", "fee-currency", "fee-currency-code"
        ),
        "timestamp_ms": _first_value(
            record, "created_at", "created-at", "trade_time", "trade-time", "ts"
        ),
    }


def _normalized_market_trade(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a public spot or swap tape record without a raw envelope."""

    return {
        "timestamp_ms": _first_value(record, "ts", "trade_time", "trade-time"),
        "side": _first_value(record, "direction", "side"),
        "price": _number_text(_first_value(record, "price", "trade_price")),
        "quantity": _number_text(
            _first_value(record, "amount", "quantity", "volume", "trade_volume")
        ),
        "trade_id": _first_value(record, "id", "trade_id", "trade-id"),
    }


def _market_trade_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten HTX's spot batches and swap trade lists into tape records."""

    records: list[dict[str, Any]] = []
    for item in _records(payload):
        nested = item.get("data")
        if isinstance(nested, list):
            records.extend(record for record in nested if isinstance(record, dict))
        else:
            records.append(item)
    return records


def _normalized_candle_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return chronological fixed-point OHLCV records for market context."""

    return [
        {
            "open_time_ms": candle.open_time_ms,
            "open": decimal_to_text(candle.open),
            "high": decimal_to_text(candle.high),
            "low": decimal_to_text(candle.low),
            "close": decimal_to_text(candle.close),
            "volume": decimal_to_text(candle.volume),
        }
        for candle in candles_from_htx(_data(payload))
    ]


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
            "index",
            "funding",
            "open_interest",
            "price_limit",
        }
        if product == "swap"
        else {"ticker", "depth"}
    )


def register_semantic_tools(mcp: Any, api: ModuleType) -> None:
    """Register the semantic facade against the existing raw server gateway."""

    quote_cache: dict[str, Decimal] = {"USDT": Decimal(1)}
    quote_cache_at = 0.0
    quote_cache_lock = asyncio.Lock()

    async def spot_usdt_quotes() -> dict[str, Decimal]:
        """Return a five-minute cache of direct spot asset/USDT closing prices."""

        nonlocal quote_cache, quote_cache_at
        now = time.monotonic()
        if now - quote_cache_at < 300:
            return quote_cache
        async with quote_cache_lock:
            now = time.monotonic()
            if now - quote_cache_at < 300:
                return quote_cache
            payload = await api.spot_get_tickers()
            quotes = {"USDT": Decimal(1)}
            for ticker in _records(payload):
                symbol = str(ticker.get("symbol", "")).upper()
                close = _first_value(ticker, "close", "last", "last_price")
                if not symbol.endswith("USDT") or close in (None, ""):
                    continue
                try:
                    quotes[symbol.removesuffix("USDT")] = Decimal(str(close))
                except InvalidOperation:
                    continue
            quote_cache = quotes
            quote_cache_at = now
            return quote_cache

    async def compact_spot_balance_assets(
        balances: Any,
    ) -> tuple[dict[str, Any], int, int, bool]:
        """Aggregate spot balances and hide dust or assets without a direct quote."""

        if not isinstance(balances, dict) or not isinstance(balances.get("list"), list):
            return {"assets": []}, 0, 0, False
        totals: dict[str, Decimal] = {}
        for record in balances["list"]:
            if not isinstance(record, dict) or not record.get("currency"):
                continue
            amount = _spot_balance_amount(record)
            if amount is None:
                continue
            asset = str(record["currency"]).upper()
            totals[asset] = totals.get(asset, Decimal(0)) + amount
        if not totals:
            return {"assets": []}, 0, 0, False
        stale_quotes = False
        try:
            quotes = await spot_usdt_quotes()
        except (api.HtxError, ToolError, InvalidOperation):
            quotes = quote_cache
            stale_quotes = True
        assets: list[dict[str, str]] = []
        dust_count = 0
        unpriced_count = 0
        for asset, amount in sorted(totals.items()):
            quote = quotes.get(asset)
            if quote is None:
                unpriced_count += 1
                continue
            value_usdt = amount * quote
            if value_usdt.copy_abs() < Decimal("0.1"):
                dust_count += 1
                continue
            assets.append(
                {
                    "asset": asset,
                    "amount": _display_decimal(amount, significant_digits=8),
                    "value_usdt": _display_decimal(value_usdt, places=2),
                }
            )
        return {"assets": assets}, dust_count, unpriced_count, stale_quotes

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
    ) -> MarketSnapshotResult:
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
        verbose: bool = False,
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

        async def collect(calls: dict[str, Any]) -> None:
            """Return every independently-readable account section available."""

            responses = await asyncio.gather(*calls.values(), return_exceptions=True)
            for field, response in zip(calls, responses):
                if isinstance(response, Exception):
                    result["warnings"].append(
                        f"{field}: {type(response).__name__}: {response}"
                    )
                    continue
                raw[field] = response
                data = _data(response)
                result[field] = (
                    _normalize_v5_balances(data, verbose=verbose)
                    if field == "balances" and product == "swap" and using_v5
                    else data
                )

        if product == "spot":
            unsupported = fields.intersection({"positions", "api_status"})
            result["warnings"].extend(
                f"{field}: field is only supported for swap account snapshots"
                for field in sorted(unsupported)
            )
            try:
                account_id = await api._resolve_spot_account_id(None)
            except (api.HtxError, ToolError) as exc:
                result["warnings"].append(f"account_id: {type(exc).__name__}: {exc}")
                if include_raw:
                    result["raw"] = raw
                return result
            result["account_id"] = account_id
            calls: dict[str, Any] = {}
            if "balances" in fields:
                calls["balances"] = api.spot_get_account_balance(account_id)
            if "open_orders" in fields:
                calls["open_orders"] = api.spot_get_open_orders(
                    account_id, api._symbol(instrument) if instrument else None
                )
            await collect(calls)
            if "balances" in result and not verbose:
                (
                    compact,
                    dust_count,
                    unpriced_count,
                    stale_quotes,
                ) = await compact_spot_balance_assets(result["balances"])
                result["balances"] = compact
                if dust_count:
                    result["filtered_dust_asset_count"] = dust_count
                if unpriced_count:
                    result["filtered_unpriced_asset_count"] = unpriced_count
                if stale_quotes:
                    result["warnings"].append(
                        "balances: using cached or unavailable spot USDT quotes"
                    )
        else:
            code = api._contract(instrument) if instrument else None
            using_v5 = api.client.config.swap_api_version == "v5"
            if margin_mode == "cross" and not using_v5:
                try:
                    account_type = await api.futures_get_account_type()
                    account_type_data = _data(account_type)
                    result["account_type"] = account_type_data
                    if (
                        isinstance(account_type_data, dict)
                        and account_type_data.get("account_type") == 2
                    ):
                        result["warnings"].append(
                            "HTX unified USDT account detected: legacy cross-margin "
                            "account, position, and order endpoints are unavailable. "
                            "This MCP server does not change account types."
                        )
                        if include_raw:
                            raw["account_type"] = account_type
                            result["raw"] = raw
                        return result
                except api.HtxError as exc:
                    result["warnings"].append(
                        f"account_type: {type(exc).__name__}: {exc}"
                    )
            calls = {}
            if "balances" in fields:
                calls["balances"] = (
                    api.v5_get_account_balance()
                    if using_v5
                    else api.futures_get_account_info(margin_mode, code)
                )
            if "positions" in fields:
                calls["positions"] = (
                    api.v5_get_positions(code)
                    if using_v5
                    else api.futures_get_positions(margin_mode, code)
                )
            if "open_orders" in fields and using_v5:
                calls["open_orders"] = api.v5_get_open_orders(code, margin_mode)
            elif "open_orders" in fields and code:
                calls["open_orders"] = api.futures_get_open_orders(code, margin_mode)
            elif "open_orders" in fields:
                result["warnings"].append(
                    "open_orders: instrument is required for legacy swap account snapshots"
                )
            if "api_status" in fields and not using_v5:
                calls["api_status"] = api.futures_get_api_trading_status()
            elif "api_status" in fields:
                result["warnings"].append(
                    "api_status: no v5 equivalent; use account and order responses for v5 state."
                )
            await collect(calls)
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
        if api.client.config.swap_api_version == "v5":
            order_type = {
                "market": "market",
                "limit": "limit",
                "post_only": "post_only",
                "ioc": "limit",
                "fok": "limit",
            }[intent.order_kind]
            return api._q(
                contract_code=contract,
                margin_mode=intent.margin_mode,
                side=intent.side,
                position_side=intent.position_side,
                type=order_type,
                volume=decimal_to_text(intent.quantity),
                price=(
                    decimal_to_text(intent.price)
                    if intent.order_kind != "market" and intent.price is not None
                    else None
                ),
                time_in_force={"ioc": "ioc", "fok": "fok"}.get(intent.order_kind),
                reduce_only=1 if intent.reduce_only or intent.action != "open" else 0,
                client_order_id=_swap_client_order_id_text(intent.client_order_id),
                tp_trigger_price=decimal_to_text(intent.take_profit.trigger_price)
                if intent.take_profit
                else None,
                tp_order_price=decimal_to_text(intent.take_profit.order_price)
                if intent.take_profit and intent.take_profit.order_price
                else None,
                tp_type=intent.take_profit.order_price_type
                if intent.take_profit
                else None,
                sl_trigger_price=decimal_to_text(intent.stop_loss.trigger_price)
                if intent.stop_loss
                else None,
                sl_order_price=decimal_to_text(intent.stop_loss.order_price)
                if intent.stop_loss and intent.stop_loss.order_price
                else None,
                sl_type=intent.stop_loss.order_price_type if intent.stop_loss else None,
            )
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
        if intent.product == "swap" and intent.client_order_id is not None:
            text = str(intent.client_order_id)
            if (
                not re.fullmatch(r"[0-9]+", text)
                or not 1 <= int(text) <= 9223372036854775807
            ):
                _check(
                    checks,
                    "error",
                    "swap_client_order_id_format",
                    "V5 and legacy swap order endpoints require client_order_id to be a decimal integer from 1 through 9223372036854775807.",
                )
        if (
            intent.product == "swap"
            and api.client.config.swap_api_version == "v5"
            and intent.leverage is not None
        ):
            _check(
                checks,
                "error",
                "v5_leverage_must_be_set_separately",
                "V5 leverage must be set with futures_v5_set_leverage before submitting this trade.",
            )
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
        rules_unavailable = False
        try:
            rules = await fetch_rules(intent.product, instrument)
        except api.HtxError as exc:
            rules_unavailable = True
            rules = {
                "product": intent.product,
                "instrument": instrument,
                "matched": None,
                "rules": None,
                "available_count": 0,
            }
            _check(
                checks,
                "error",
                "instrument_rules_unavailable",
                f"Could not retrieve HTX instrument rules: {type(exc).__name__}: {exc}",
            )
        rule = rules.get("matched")
        if rule is None and not rules_unavailable:
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
            _check(
                checks,
                "error",
                "market_price_unavailable",
                "Could not retrieve a current HTX ticker price for pre-trade validation.",
            )
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
        include_raw: IncludeRaw = False,
    ) -> MarketSnapshotResult:
        """Return one compact, concurrently collected market snapshot for analysis or execution.

        Prefer this aggregate over several low-level market calls. For technical
        analysis, call htx_get_technical_indicators instead of requesting raw K-lines.
        """

        return await fetch_market(
            product, instrument, profile, include, "1hour", 100, include_raw
        )

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_technical_indicators(
        product: Product,
        instrument: Instrument,
        indicators: Annotated[
            list[str],
            Field(
                min_length=1,
                description="Indicators to calculate. Use sma:N (or ma:N), ema:N, rsi:N, atr:N, volume_sma:N, bbands:N,multiplier, macd:fast,slow,signal, or kdj:N,k_smoothing,d_smoothing. Example: ['sma:20', 'sma:60', 'rsi:14', 'macd:12,26,9']. Values are calculated deterministically from completed HTX candles.",
            ),
        ],
        period: Annotated[
            str,
            Field(
                description="HTX candle interval: 1min, 5min, 15min, 30min, 60min, 4hour, 1day, 1week, or 1mon."
            ),
        ] = "60min",
        candle_size: Annotated[
            int,
            Field(
                ge=1,
                le=2000,
                description="Maximum HTX candles to fetch. It must cover the largest requested indicator lookback; 300 is suitable for most standard indicators.",
            ),
        ] = 300,
        include_current_candle: Annotated[
            bool,
            Field(
                description="Include the still-forming candle when true. Defaults to false so decisions use only completed candles and do not repaint.",
            ),
        ] = False,
    ) -> TechnicalIndicatorsResult:
        """Return only the requested, Decimal-calculated technical indicators.

        The model chooses which named indicators to inspect; it never calculates
        them itself. Results are display-rounded only at response time: EMA uses
        candle price precision plus one decimal, MACD uses it plus three, and
        RSI/KDJ use two decimal places. Raw K-lines remain available solely through the opt-in advanced
        compatibility toolset for research and diagnostics.
        """

        code = (
            api._symbol(instrument) if product == "spot" else api._contract(instrument)
        )
        interval_ms = period_ms(period)
        payload = (
            await api.spot_get_klines(code, period=period, size=candle_size)
            if product == "spot"
            else await api.futures_get_klines(code, period=period, size=candle_size)
        )
        candles = candles_from_htx(_data(payload))
        omitted = 0
        if not include_current_candle:
            now_ms = int(time.time() * 1000)
            completed = [
                candle
                for candle in candles
                if candle.open_time_ms + interval_ms <= now_ms
            ]
            omitted = len(candles) - len(completed)
            candles = completed
        return {
            "product": product,
            "instrument": code,
            "period": period,
            "as_of_ms": int(time.time() * 1000),
            "completed_candles": len(candles),
            "omitted_incomplete_candles": omitted,
            "latest_completed_open_ms": candles[-1].open_time_ms if candles else None,
            "indicators": calculate_indicators(candles, indicators),
        }

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_wait_for_market_event(
        conditions: Annotated[
            list[MarketWaitCondition],
            Field(
                min_length=1,
                max_length=8,
                description="Declarative conditions to wait for. Each condition may override product and instrument, allowing independent markets in one wait. A condition checks last_price or a supported technical indicator (for example indicator='rsi:14' or 'macd:12,26,9' with component='histogram'). Arbitrary code and expressions are not accepted.",
            ),
        ],
        product: Product | None = None,
        instrument: Instrument | None = None,
        match: Annotated[
            Literal["any", "all"],
            Field(
                description="Wake when any condition matches, or only when all match."
            ),
        ] = "any",
        candle_period: Annotated[
            str,
            Field(
                description="HTX candle interval used for indicator conditions: 1min, 5min, 15min, 30min, 60min, 4hour, 1day, 1week, or 1mon."
            ),
        ] = "60min",
        candle_size: Annotated[
            int,
            Field(
                ge=1,
                le=2000,
                description="Maximum candles fetched for indicator conditions. It must cover every requested indicator lookback.",
            ),
        ] = 300,
        timeout_seconds: Annotated[
            int,
            Field(
                ge=1,
                le=10800,
                description="Hard maximum blocking duration (three hours). The budget includes polls and sleeps. Prefer a condition-driven wait sized to the monitoring horizon; timeout is a safety/review boundary, not a scheduled prompt to manually inspect the market. Configure the MCP host tool deadline and outer yield_time_ms longer than the chosen duration; with a three-hour host deadline, leave response margin below this ceiling so this tool, rather than an intermediate host yield, is the wake-up source.",
            ),
        ] = 60,
        poll_interval_seconds: Annotated[
            int,
            Field(
                ge=1,
                le=60,
                description="Target minimum interval between read-only poll starts. A larger interval reduces API use but increases wake latency.",
            ),
        ] = 5,
    ) -> MarketWaitResult:
        """Block until bounded declarative market conditions match or the timeout expires.

        This safe MCP wait primitive performs read-only calls, executes no
        caller-supplied code, has a fixed deadline, and returns compact evidence.
        A future WebSocket market daemon can satisfy the identical contract.

        Important: while this call is pending, the agent receives no intermediate
        market updates and cannot react to them. For an uninterrupted wait, set
        the outer host ``yield_time_ms`` longer than ``timeout_seconds * 1000``
        (with response margin) and set the host tool deadline longer still. The
        MCP result then becomes the sole wake-up source. Do not run multiple
        waits in parallel: put all independent conditions in this one call and
        use ``match='any'`` when any condition should wake the agent; then
        re-check market snapshots after every return.
        """

        period_ms(candle_period)
        markets: dict[tuple[str, str], dict[str, Any]] = {}
        condition_markets: dict[int, tuple[str, str]] = {}
        condition_indicators: dict[int, str] = {}
        for index, condition in enumerate(conditions):
            condition_product = condition.product or product
            condition_instrument = condition.instrument or instrument
            if condition_product is None or condition_instrument is None:
                raise ToolError(
                    "Each condition requires product and instrument, either on the condition or as top-level defaults"
                )
            code = (
                api._symbol(condition_instrument)
                if condition_product == "spot"
                else api._contract(condition_instrument)
            )
            market = (condition_product, code)
            markets.setdefault(
                market, {"indices": [], "indicators": set(), "ticker": False}
            )
            markets[market]["indices"].append(index)
            condition_markets[index] = market
            if condition.indicator is None:
                markets[market]["ticker"] = True
                continue
            canonical = canonical_indicator_spec(condition.indicator)
            if condition.component not in indicator_components(canonical):
                raise ToolError(
                    f"Indicator '{canonical}' does not provide component "
                    f"'{condition.component}'"
                )
            condition_indicators[index] = canonical
            markets[market]["indicators"].add(canonical)
        multiple_markets = len(markets) > 1

        def observation_key(index: int) -> str:
            condition = conditions[index]
            metric_key = (
                "last_price"
                if condition.metric == "last_price"
                else f"{condition_indicators[index]}/{condition.component}"
            )
            if not multiple_markets:
                return metric_key
            market_product, market_code = condition_markets[index]
            return f"{market_product}:{market_code}/{metric_key}"

        async def poll_market(
            market: tuple[str, str], requirements: dict[str, Any]
        ) -> tuple[tuple[str, str], dict[str, Any]]:
            market_product, market_code = market
            calls: dict[str, Any] = {}
            if requirements["ticker"]:
                calls["ticker"] = (
                    api.spot_get_ticker(market_code)
                    if market_product == "spot"
                    else api.futures_get_ticker(market_code)
                )
            if requirements["indicators"]:
                calls["klines"] = (
                    api.spot_get_klines(
                        market_code, period=candle_period, size=candle_size
                    )
                    if market_product == "spot"
                    else api.futures_get_klines(
                        market_code, period=candle_period, size=candle_size
                    )
                )
            responses = await asyncio.gather(*calls.values())
            return market, dict(zip(calls, responses))

        started_monotonic = time.monotonic()
        started_at_ms = int(time.time() * 1000)
        deadline = started_monotonic + timeout_seconds
        polls = 0
        successful_polls = 0
        failed_polls = 0
        last_success_at_ms: int | None = None
        warnings: list[Any] = []
        last_observations: dict[str, str | None] = {}

        while True:
            observations: dict[str, tuple[Decimal, str | None, int | None]] = {}
            poll_started = time.monotonic()
            try:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    polls += 1
                    results = await asyncio.wait_for(
                        asyncio.gather(
                            *(
                                poll_market(market, requirements)
                                for market, requirements in markets.items()
                            ),
                            return_exceptions=True,
                        ),
                        timeout=remaining,
                    )
                    successful = 0
                    failures: list[Exception] = []
                    for market_result in results:
                        if isinstance(market_result, Exception):
                            failures.append(market_result)
                            continue
                        market, payloads = market_result
                        successful += 1
                        requirements = markets[market]
                        if "ticker" in payloads:
                            last = _ticker(payloads["ticker"])["last"]
                            if last is None:
                                failures.append(
                                    ToolError("HTX ticker did not include a last price")
                                )
                            else:
                                for index in requirements["indices"]:
                                    if conditions[index].metric == "last_price":
                                        observations[observation_key(index)] = (
                                            Decimal(last),
                                            None,
                                            None,
                                        )
                        if "klines" in payloads:
                            candles = candles_from_htx(_data(payloads["klines"]))
                            now_ms = int(time.time() * 1000)
                            candles = [
                                candle
                                for candle in candles
                                if candle.open_time_ms + period_ms(candle_period)
                                <= now_ms
                            ]
                            indicator_values = calculate_indicator_values(
                                candles, sorted(requirements["indicators"])
                            )
                            price_places = price_decimal_places(candles)
                            for index in requirements["indices"]:
                                canonical = condition_indicators.get(index)
                                if canonical is None:
                                    continue
                                value = indicator_values[canonical].get(
                                    conditions[index].component
                                )
                                if value is not None:
                                    observations[observation_key(index)] = (
                                        value,
                                        canonical,
                                        price_places,
                                    )
                    if successful:
                        successful_polls += 1
                        last_success_at_ms = int(time.time() * 1000)
                    if failures:
                        failed_polls += 1
                        if len(warnings) < 10:
                            failure = failures[0]
                            warnings.append(
                                {
                                    "poll": polls,
                                    "error_type": type(failure).__name__,
                                    "message": str(failure) or "market request failed",
                                    "retryable": isinstance(
                                        failure, (asyncio.TimeoutError, HtxApiError)
                                    ),
                                    "at_ms": int(time.time() * 1000),
                                }
                            )
            except (
                asyncio.TimeoutError,
                HtxApiError,
                ToolError,
                ValueError,
                InvalidOperation,
            ) as exc:
                failed_polls += 1
                if len(warnings) < 10:
                    message = str(exc) or "operation timed out"
                    warnings.append(
                        {
                            "poll": polls,
                            "error_type": type(exc).__name__,
                            "message": message,
                            "retryable": isinstance(
                                exc, (asyncio.TimeoutError, HtxApiError)
                            ),
                            "at_ms": int(time.time() * 1000),
                        }
                    )
                elif len(warnings) == 10:
                    warnings.append("Additional failed polls are omitted.")

            if observations:
                last_observations = {
                    key: (
                        decimal_to_text(value)
                        if specification is None
                        else format_indicator_value(
                            specification,
                            value,
                            price_places=price_places,
                        )
                    )
                    for key, (
                        value,
                        specification,
                        price_places,
                    ) in observations.items()
                }
            matched_conditions: list[int] = []
            for index, condition in enumerate(conditions):
                key = observation_key(index)
                observed = observations.get(key)
                if observed is None:
                    continue
                matched = (
                    observed[0] >= condition.value
                    if condition.operator == "gte"
                    else observed[0] <= condition.value
                )
                if matched:
                    matched_conditions.append(index)
            is_triggered = (
                bool(matched_conditions)
                if match == "any"
                else len(matched_conditions) == len(conditions)
            )
            now_monotonic = time.monotonic()
            if is_triggered or now_monotonic >= deadline:
                status: Literal["triggered", "timed_out", "data_unavailable"]
                if is_triggered:
                    status = "triggered"
                elif not last_observations:
                    status = "data_unavailable"
                else:
                    status = "timed_out"
                finished_at_ms = int(time.time() * 1000)
                return {
                    "status": status,
                    "product": next(iter(markets))[0] if not multiple_markets else None,
                    "instrument": (
                        next(iter(markets))[1] if not multiple_markets else None
                    ),
                    "match": match,
                    "started_at_ms": started_at_ms,
                    "finished_at_ms": finished_at_ms,
                    "elapsed_ms": round((now_monotonic - started_monotonic) * 1000),
                    "polls": polls,
                    "requested_timeout_ms": timeout_seconds * 1000,
                    "effective_deadline_ms": started_at_ms + timeout_seconds * 1000,
                    "poll_attempts": polls,
                    "successful_polls": successful_polls,
                    "failed_polls": failed_polls,
                    "last_success_at_ms": last_success_at_ms,
                    "observation_age_ms": (
                        max(0, int(time.time() * 1000) - last_success_at_ms)
                        if last_success_at_ms is not None
                        else None
                    ),
                    "observations": last_observations,
                    "matched_conditions": matched_conditions,
                    "warnings": warnings,
                }
            cadence_delay = poll_interval_seconds - (time.monotonic() - poll_started)
            await asyncio.sleep(min(max(0, cadence_delay), deadline - now_monotonic))

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
        verbose: Annotated[
            bool,
            Field(
                description="Return unfiltered per-record balance details for diagnostics. Defaults to false for a compact valued asset view."
            ),
        ] = False,
    ) -> AccountSnapshotResult:
        """Return a compact authenticated snapshot of balances, positions, and orders.

        This is read-only and requires API credentials.
        """

        return await fetch_account(
            product, instrument, margin_mode, include, include_raw, verbose
        )

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_portfolio_snapshot(
        margin_mode: MarginMode = "isolated",
        include_open_orders: Annotated[
            bool,
            Field(
                description="Include active orders. For legacy swaps this requires a per-contract account snapshot, so the result reports that limitation as a warning."
            ),
        ] = True,
        include_zero_balances: Annotated[
            bool,
            Field(
                description="Include zero-valued spot balance records for diagnostics. Defaults to false to keep the compact snapshot bounded."
            ),
        ] = False,
        verbose: Annotated[
            bool,
            Field(
                description="Return unfiltered per-record balance details for diagnostics. Defaults to false for a compact valued asset view."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Return one compact cross-product portfolio snapshot for account review.

        It concurrently reads spot and USDT-swap balances and positions. V5 swap
        accounts also return open orders across every contract, so this is the
        preferred pre-session portfolio check; no market data is fetched.
        """

        spot_fields = ["balances"]
        swap_fields = ["balances", "positions"]
        if include_open_orders:
            spot_fields.append("open_orders")
            swap_fields.append("open_orders")
        spot, swap = await asyncio.gather(
            fetch_account("spot", None, margin_mode, spot_fields, False, verbose),
            fetch_account("swap", None, margin_mode, swap_fields, False, verbose),
            return_exceptions=True,
        )
        result: dict[str, Any] = {
            "as_of_ms": int(time.time() * 1000),
            "accounts": {},
            "warnings": [],
        }
        for product, snapshot in (("spot", spot), ("swap", swap)):
            if isinstance(snapshot, Exception):
                result["warnings"].append(
                    f"{product}: {type(snapshot).__name__}: {snapshot}"
                )
            else:
                if (
                    verbose
                    and product == "spot"
                    and not include_zero_balances
                    and "balances" in snapshot
                ):
                    compact, filtered = _compact_spot_balances(snapshot["balances"])
                    snapshot = {**snapshot, "balances": compact}
                    if filtered:
                        snapshot["filtered_zero_balance_count"] = filtered
                result["accounts"][product] = snapshot
                result["warnings"].extend(
                    f"{product}: {warning}" for warning in snapshot.get("warnings", [])
                )
        return result

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_market_context(
        product: Product,
        instrument: Instrument,
        include: Annotated[
            list[Literal["candles", "recent_trades", "funding_history"]] | None,
            Field(
                description="Optional context sections. Omit for candles and recent trades; funding_history is available only for swaps."
            ),
        ] = None,
        candle_period: Annotated[
            str,
            Field(
                description="HTX candle interval for candles: 1min, 5min, 15min, 30min, 60min, 4hour, 1day, 1week, or 1mon."
            ),
        ] = "60min",
        candle_size: Annotated[
            int,
            Field(ge=1, le=500, description="Number of normalized candles (1-500)."),
        ] = 100,
        recent_trade_limit: Annotated[
            int,
            Field(
                ge=1, le=200, description="Number of normalized tape records (1-200)."
            ),
        ] = 50,
        funding_history_limit: Annotated[
            int,
            Field(
                ge=1, le=50, description="Number of swap funding-rate records (1-50)."
            ),
        ] = 20,
    ) -> dict[str, Any]:
        """Return bounded research context without exposing raw HTX envelopes.

        Use this when indicators alone are insufficient and a trader needs
        inspectable OHLCV, a recent public trade tape, or swap funding history.
        It is a REST snapshot, not a low-latency stream.
        """

        period_ms(candle_period)
        code = (
            api._symbol(instrument) if product == "spot" else api._contract(instrument)
        )
        fields = set(include or ["candles", "recent_trades"])
        calls: dict[str, Any] = {}
        if "candles" in fields:
            calls["candles"] = (
                api.spot_get_klines(code, period=candle_period, size=candle_size)
                if product == "spot"
                else api.futures_get_klines(
                    code, period=candle_period, size=candle_size
                )
            )
        if "recent_trades" in fields:
            calls["recent_trades"] = (
                api.spot_get_recent_trades(code, size=recent_trade_limit)
                if product == "spot"
                else api.futures_get_recent_trades(code, size=recent_trade_limit)
            )
        warnings: list[str] = []
        if "funding_history" in fields:
            if product == "swap":
                calls["funding_history"] = api.futures_get_historical_funding_rate(
                    code, page_size=funding_history_limit
                )
            else:
                warnings.append("funding_history: field is only supported for swaps")
        responses = await asyncio.gather(*calls.values(), return_exceptions=True)
        data: dict[str, Any] = {}
        for name, response in zip(calls, responses):
            if isinstance(response, Exception):
                warnings.append(f"{name}: {type(response).__name__}: {response}")
            elif name == "candles":
                data[name] = _normalized_candle_records(response)
            elif name == "recent_trades":
                data[name] = [
                    _normalized_market_trade(record)
                    for record in _market_trade_records(response)[:recent_trade_limit]
                ]
            else:
                data[name] = _records(response)[:funding_history_limit]
        return {
            "product": product,
            "instrument": code,
            "as_of_ms": int(time.time() * 1000),
            "data": data,
            "warnings": warnings,
        }

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_trade_history(
        product: Product,
        instrument: Instrument,
        order_id: Annotated[
            str | None,
            Field(
                description="Optional exchange order ID. When supplied, return fills for that order rather than a paged instrument history."
            ),
        ] = None,
        margin_mode: MarginMode = "isolated",
        start_time: Annotated[
            int | None,
            Field(
                description="Optional inclusive millisecond start time for a paged history query."
            ),
        ] = None,
        end_time: Annotated[
            int | None,
            Field(
                description="Optional inclusive millisecond end time for a paged history query."
            ),
        ] = None,
        cursor: Annotated[
            str | int | None,
            Field(
                description="Opaque cursor from the previous result. Omit on the first page."
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1, le=100, description="Maximum normalized fills to return (1-100)."
            ),
        ] = 50,
        direction: Annotated[
            Literal["next", "prev"],
            Field(
                description="Page direction: prev returns older records and next advances from a cursor."
            ),
        ] = "prev",
        include_raw: IncludeRaw = False,
    ) -> dict[str, Any]:
        """Return compact executed-fill history for one instrument, not order history.

        Traders normally review fills to reconcile an order, calculate realized
        entry/exit prices and fees, or inspect recent execution quality. Provide
        ``order_id`` for reconciliation; otherwise use a bounded time window and
        cursor to page a recent instrument history. The exchange retention window
        is 48 hours for spot and legacy swaps, and three days for v5 swaps.
        """

        code = (
            api._symbol(instrument) if product == "spot" else api._contract(instrument)
        )
        if order_id is not None and (
            start_time is not None or end_time is not None or cursor is not None
        ):
            raise ToolError(
                "order_id cannot be combined with time-range or cursor pagination"
            )
        if order_id is not None:
            order_id = api._text(order_id, "order_id")

        if product == "spot":
            if order_id is not None:
                payload = await api.spot_get_order_match_results(order_id)
            else:
                api._validate_time_range(
                    start_time,
                    end_time,
                    max_window_ms=48 * 60 * 60 * 1000,
                    max_age_ms=180 * 24 * 60 * 60 * 1000,
                )
                payload = await api.spot_get_match_results(
                    code,
                    size=limit,
                    start_time=start_time,
                    end_time=end_time,
                    from_match_id=str(cursor) if cursor is not None else None,
                    direct=direction,
                )
        elif api.client.config.swap_api_version == "v5":
            api._validate_time_range(
                start_time,
                end_time,
                max_window_ms=3 * 24 * 60 * 60 * 1000,
                max_age_ms=3 * 24 * 60 * 60 * 1000,
            )
            if cursor is not None:
                try:
                    from_cursor = int(cursor)
                except (TypeError, ValueError) as exc:
                    raise ToolError("v5 cursor must be an integer") from exc
                if from_cursor < 0:
                    raise ToolError("v5 cursor must be non-negative")
            else:
                from_cursor = None
            payload = await api.v5_get_trade_history(
                contract_code=code,
                order_id=order_id,
                start_time=start_time,
                end_time=end_time,
                from_cursor=from_cursor,
                limit=limit,
                direct=direction,
            )
        else:
            if order_id is not None:
                payload = await api.futures_get_order_detail(
                    code, order_id, margin_mode=margin_mode, page_size=limit
                )
            else:
                api._validate_time_range(
                    start_time,
                    end_time,
                    max_window_ms=48 * 60 * 60 * 1000,
                )
                if cursor is not None:
                    try:
                        from_id = int(cursor)
                    except (TypeError, ValueError) as exc:
                        raise ToolError(
                            "legacy swap cursor must be an integer"
                        ) from exc
                    if from_id <= 0:
                        raise ToolError("legacy swap cursor must be positive")
                else:
                    from_id = None
                payload = await api.futures_get_match_results(
                    code,
                    margin_mode=margin_mode,
                    start_time=start_time,
                    end_time=end_time,
                    from_id=from_id,
                    size=limit,
                    direct=direction,
                )

        data = _data(payload)
        records = _records(payload)[:limit]
        result: dict[str, Any] = {
            "product": product,
            "instrument": code,
            "margin_mode": margin_mode if product == "swap" else None,
            "records": [_normalized_trade(record) for record in records],
            "returned": len(records),
            "next_cursor": _first_value(
                payload, "next_id", "next-id", "next_cursor", "next-cursor"
            )
            or (
                _first_value(data, "next_id", "next-id", "next_cursor", "next-cursor")
                if isinstance(data, dict)
                else None
            ),
        }
        if include_raw:
            result["raw"] = payload
        return result

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

    async def plan_spot_margin_action(action: SpotMarginAction) -> SpotMarginPlanResult:
        """Normalize a margin funding action and preflight borrow capacity."""

        instrument = (
            api._symbol(action.instrument) if action.instrument is not None else None
        )
        currency = api._text(action.currency, "currency").lower()
        amount = decimal_to_text(action.amount)
        checks: list[dict[str, str]] = []
        warnings: list[str] = []
        stem = "margin" if action.margin_mode == "isolated" else "cross-margin"
        if action.action in {"transfer_in", "transfer_out"}:
            direction = "in" if action.action == "transfer_in" else "out"
            path = (
                f"/v1/dw/transfer-{direction}/margin"
                if action.margin_mode == "isolated"
                else f"/v1/{stem}/transfer-{direction}"
            )
            body = api._q(symbol=instrument, currency=currency, amount=amount)
        elif action.action == "borrow":
            path = f"/v1/{stem}/orders"
            body = api._q(symbol=instrument, currency=currency, amount=amount)
            try:
                loan_info = await api.spot_margin_get_loan_info(
                    action.margin_mode, instrument
                )
                records = _records(loan_info)
                candidates: list[dict[str, Any]] = []
                for record in records:
                    if action.margin_mode == "isolated":
                        if str(record.get("symbol", "")).lower() != instrument:
                            continue
                        candidates.extend(
                            item
                            for item in record.get("currencies", [])
                            if isinstance(item, dict)
                        )
                    else:
                        candidates.append(record)
                quota = next(
                    (
                        item
                        for item in candidates
                        if str(item.get("currency", "")).lower() == currency
                    ),
                    None,
                )
                if quota is None:
                    _check(
                        checks,
                        "error",
                        "loan_currency_unavailable",
                        "HTX did not report a loan quota for this currency.",
                    )
                else:
                    minimum = _rule_decimal(quota, "min-loan-amt")
                    available = _rule_decimal(quota, "loanable-amt")
                    if minimum is not None and action.amount < minimum:
                        _check(
                            checks,
                            "error",
                            "loan_below_minimum",
                            f"amount is below HTX minimum loan amount {decimal_to_text(minimum)}.",
                        )
                    if available is not None and action.amount > available:
                        _check(
                            checks,
                            "error",
                            "loan_above_available",
                            f"amount exceeds HTX currently loanable amount {decimal_to_text(available)}.",
                        )
            except (api.HtxError, ToolError) as exc:
                warnings.append(f"loan_info: {type(exc).__name__}: {exc}")
        else:
            path = f"/v1/{stem}/orders/{api._text(action.loan_order_id, 'loan_order_id')}/repay"
            body = {"amount": amount}
        return {
            "status": "blocked"
            if any(item["severity"] == "error" for item in checks)
            else "ready",
            "action": action.action,
            "margin_mode": action.margin_mode,
            "request": {"method": "POST", "path": path, "body": body},
            "checks": checks,
            "warnings": warnings,
        }

    @mcp.tool(annotations=api.READ, toolsets={"analysis"})
    async def htx_get_spot_margin_snapshot(
        margin_mode: Annotated[
            Literal["isolated", "cross"],
            Field(
                description="Spot-margin mode: isolated per symbol or cross across currencies."
            ),
        ],
        instrument: Annotated[
            str | None,
            Field(
                description="Required isolated-margin symbol; omit for cross margin."
            ),
        ] = None,
        include_raw: IncludeRaw = False,
    ) -> dict[str, Any]:
        """Return compact spot-margin balances, debt/risk fields, and current loan quotas.

        This read-only preflight is designed to precede borrowing, repayment, or any margin-funded trade.
        """

        if margin_mode == "isolated" and instrument is None:
            raise ToolError("instrument is required for isolated spot margin")
        if margin_mode == "cross" and instrument is not None:
            raise ToolError("instrument must be omitted for cross spot margin")
        symbol = api._symbol(instrument) if instrument else None
        account, loan_info = await asyncio.gather(
            api.spot_margin_get_account(margin_mode, symbol),
            api.spot_margin_get_loan_info(margin_mode, symbol),
            return_exceptions=True,
        )
        result: dict[str, Any] = {
            "margin_mode": margin_mode,
            "instrument": symbol,
            "as_of_ms": int(time.time() * 1000),
            "warnings": [],
        }
        raw: dict[str, Any] = {}
        for name, response in (("account", account), ("loan_info", loan_info)):
            if isinstance(response, Exception):
                result["warnings"].append(
                    f"{name}: {type(response).__name__}: {response}"
                )
            else:
                result[name] = _data(response)
                raw[name] = response
        if include_raw:
            result["raw"] = raw
        return result

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_plan_spot_margin_action(
        action: SpotMarginAction,
    ) -> SpotMarginPlanResult:
        """Validate and normalize one spot-margin transfer, borrow, or targeted repayment.

        Borrow plans check HTX's live currency quota where available. This does
        not replace the account/debt snapshot; a ready plan is not an execution
        request.
        """

        return await plan_spot_margin_action(action)

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_execute_spot_margin_action(
        action: SpotMarginAction, confirm: Confirm = False
    ) -> SpotMarginExecutionResult:
        """Revalidate then execute one confirmed spot-margin funding action.

        Use the snapshot and plan tools first. After a confirmed mutation, refresh the snapshot because interest, risk, and transferable balances may have changed.
        """

        plan = await plan_spot_margin_action(action)
        if plan["status"] == "blocked":
            return {
                "plan": plan,
                "execution": {
                    "executed": False,
                    "dry_run": True,
                    "reason": "validation_blocked",
                },
            }
        try:
            execution = await api._mutation(
                "htx_execute_spot_margin_action",
                plan["request"]["path"],
                plan["request"]["body"],
                confirm,
            )
        except api.HtxError as exc:
            execution = {
                "executed": False,
                "dry_run": False,
                "ok": False,
                "error": api._diagnostic_error(exc),
            }
        return {"plan": plan, "execution": execution}

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_validate_trade_intent(intent: TradeIntent) -> TradeValidationResult:
        """Validate one spot or swap intent against fresh HTX rules and last price.

        Returns precision, minimum, order-style, and protection-direction
        checks without constructing a request. It does not assess account
        balances, positions, open orders, authorization, or strategy risk.
        """

        return validation_summary(await validate(intent))

    @mcp.tool(annotations=api.READ, toolsets={"planning"})
    async def htx_preview_trade(intent: TradeIntent) -> TradePreviewResult:
        """Revalidate one intent and return its normalized dry-run request.

        This standalone call fetches fresh HTX rules and last price. It does
        not submit an order or assess account state, authorization, or strategy
        risk.
        """

        result = await validate(intent)
        result["execution"] = {"executed": False, "dry_run": True}
        return result

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_submit_trade(
        intent: TradeIntent, confirm: Confirm = False
    ) -> TradeSubmissionResult:
        """Revalidate and submit one confirmed normalized spot or swap intent.

        It refreshes HTX rules and last price, but does not independently
        inspect balances, positions, open orders, authorization, or strategy
        risk. Reconcile the accepted order after submission.
        """

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
            else (
                "/v5/trade/order"
                if api.client.config.swap_api_version == "v5"
                else api._swap_endpoint("order", intent.margin_mode)
            )
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

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_submit_trade_batch(
        intents: Annotated[
            list[TradeIntent],
            Field(
                min_length=1,
                max_length=10,
                description="1-10 V5 USDT-swap trade intents. Every item must use the same contract and margin mode; all are validated before one batch request is made.",
            ),
        ],
        confirm: Confirm = False,
    ) -> BatchTradeSubmissionResult:
        """Validate then submit up to ten V5 swap orders in one atomic-intent batch.

        HTX may accept some items and reject others, so inspect every returned item
        and reconcile accepted orders individually. This tool is unavailable for
        legacy swap accounts; use the opt-in advanced compatibility tools there.
        """

        if api.client.config.swap_api_version != "v5":
            raise ToolError(
                "htx_submit_trade_batch requires HTX_SWAP_API_VERSION=v5; legacy batch orders remain in the advanced toolset"
            )
        if any(intent.product != "swap" for intent in intents):
            raise ToolError("htx_submit_trade_batch accepts USDT-swap intents only")
        contracts = {api._contract(intent.instrument) for intent in intents}
        margin_modes = {intent.margin_mode for intent in intents}
        if len(contracts) != 1 or len(margin_modes) != 1:
            raise ToolError(
                "all batch intents must use the same swap contract and margin_mode"
            )
        client_ids = [
            intent.client_order_id for intent in intents if intent.client_order_id
        ]
        if len(client_ids) != len(set(map(str, client_ids))):
            raise ToolError("each non-empty client_order_id in a batch must be unique")

        validations = [await validate(intent) for intent in intents]
        if any(result["status"] == "blocked" for result in validations):
            return {
                "validations": validations,
                "execution": {
                    "executed": False,
                    "dry_run": True,
                    "reason": "validation_blocked",
                },
            }
        requests = [await build_request(intent, False) for intent in intents]
        try:
            execution = await api._mutation(
                "htx_submit_trade_batch",
                "/v5/trade/batch_orders",
                requests,
                confirm,
            )
        except api.HtxError as exc:
            execution = {
                "executed": False,
                "dry_run": False,
                "ok": False,
                "error": api._diagnostic_error(exc),
            }
        return {"validations": validations, "execution": execution}

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
        try:
            client_order_id = _client_order_id(product, client_order_id)
        except ToolError as exc:
            return {
                "product": product,
                "instrument": instrument,
                "status": "blocked",
                "order": None,
                "error": {
                    "error_type": "ValidationError",
                    "message": str(exc),
                    "retryable": False,
                },
            }
        try:
            if product == "spot":
                if client_order_id and not order_id:
                    payload = await api.spot_get_order_by_client_id(client_order_id)
                else:
                    payload = await api.spot_get_order(order_id or "")
            else:
                if api.client.config.swap_api_version == "v5":
                    payload = await api.v5_get_order(
                        api._contract(instrument),
                        order_id=order_id,
                        client_order_id=client_order_id,
                        margin_mode=margin_mode,
                    )
                else:
                    payload = await api.futures_get_order_info(
                        api._contract(instrument),
                        order_id=order_id,
                        margin_mode=margin_mode,
                        client_order_id=client_order_id,
                    )
        except api.HtxError as exc:
            error = _read_error(api, exc)
            return {
                "product": product,
                "instrument": instrument,
                "status": "not_found" if error.get("http_status") == 404 else "error",
                "order": None,
                "error": error,
            }
        return {
            "product": product,
            "instrument": instrument,
            "status": "found",
            "order": _normalized_order_id(_data(payload)),
        }

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
            path = (
                "/v5/trade/cancel_order"
                if api.client.config.swap_api_version == "v5"
                else api._swap_endpoint("cancel", margin_mode)
            )
            body = api._swap_body(
                api._contract(instrument),
                order_id=order_id,
                client_order_id=str(client_order_id)
                if client_order_id is not None
                else None,
            )
        return await api._mutation("htx_cancel_trade", path, body, confirm)

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_cancel_trades(
        product: Product,
        instrument: Instrument,
        order_ids: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=50,
                description="Exchange order IDs to cancel. Spot accepts 1-50 IDs; swap accepts 1-10 IDs per HTX batch request.",
            ),
        ],
        margin_mode: MarginMode = "isolated",
        confirm: Confirm = False,
    ) -> ExecutionResult:
        """Cancel multiple normalized spot or swap orders in one exchange request.

        A batch response can contain both successes and failures. Reconcile every
        supplied ID afterward; do not retry the whole batch after a timeout.
        """

        normalized_ids = [
            api._text(order_id, "order_ids item") for order_id in order_ids
        ]
        if product == "spot":
            return await api._mutation(
                "htx_cancel_trades",
                "/v1/order/orders/batchcancel",
                {"order-ids": normalized_ids},
                confirm,
            )
        if len(normalized_ids) > 10:
            raise ToolError("swap batch cancellation accepts at most 10 order IDs")
        contract = api._contract(instrument)
        if api.client.config.swap_api_version == "v5":
            path = "/v5/trade/cancel_batch_orders"
            body = {"contract_code": contract, "order_id": normalized_ids}
        else:
            path = api._swap_endpoint("cancel", margin_mode)
            body = {"contract_code": contract, "order_id": ",".join(normalized_ids)}
        return await api._mutation("htx_cancel_trades", path, body, confirm)

    @mcp.tool(annotations=api.WRITE, toolsets={"execution"})
    async def htx_cancel_open_trades(
        product: Product,
        instrument: Instrument | None = None,
        margin_mode: MarginMode = "isolated",
        account_id: Annotated[
            str | None,
            Field(
                description="Optional spot account ID. Omit to use HTX_SPOT_ACCOUNT_ID or resolve the unique working spot account; ignored for swaps."
            ),
        ] = None,
        confirm: Confirm = False,
    ) -> ExecutionResult:
        """Cancel all open orders for one product, scoped to an instrument when supplied.

        For swaps an instrument is required to prevent an account-wide cancellation.
        For spot, omitting it deliberately targets all open orders in the account.
        """

        if product == "spot":
            body = api._q(
                **{
                    "account-id": await api._resolve_spot_account_id(account_id),
                    "symbol": api._symbol(instrument) if instrument else None,
                    "size": 100,
                }
            )
            return await api._mutation(
                "htx_cancel_open_trades",
                "/v1/order/orders/batchCancelOpenOrders",
                body,
                confirm,
            )
        if instrument is None:
            raise ToolError("instrument is required when cancelling all swap orders")
        contract = api._contract(instrument)
        path = (
            "/v5/trade/cancel_all_orders"
            if api.client.config.swap_api_version == "v5"
            else api._swap_endpoint("cancelall", margin_mode)
        )
        return await api._mutation(
            "htx_cancel_open_trades", path, {"contract_code": contract}, confirm
        )

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
        position_side: Annotated[
            Literal["long", "short", "both"],
            Field(
                description="V5 position side: use long or short in hedge mode; both is only for one-way mode."
            ),
        ] = "both",
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
        """Submit a confirmed, reduce-only USDT-swap close for the stated quantity.

        This does not discover or close an entire position automatically. Check
        current position state first and reconcile the final order afterward.
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
            position_side=position_side,
        )
        return await htx_submit_trade(intent, confirm)

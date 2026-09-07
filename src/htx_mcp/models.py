"""Stable, product-neutral input models for semantic HTX tools."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator
from typing_extensions import NotRequired, TypedDict

DecimalAmount = Annotated[
    Decimal,
    Field(
        gt=0,
        description="Positive decimal amount. For spot buy-market this is quote-currency value; for other spot orders it is base-currency quantity; for swaps it is contract volume. Prefer a decimal string such as '0.001' to preserve exact precision.",
    ),
]
DecimalPrice = Annotated[
    Decimal,
    Field(
        gt=0,
        description="Positive decimal price. Prefer a decimal string such as '60000.125' to avoid binary floating-point rounding.",
    ),
]
SnapshotField = Literal[
    "ticker",
    "depth",
    "index",
    "funding",
    "open_interest",
    "price_limit",
    "contracts",
]
MarginMode = Annotated[
    Literal["isolated", "cross"],
    Field(description="Swap margin mode: isolated or cross."),
]
Confirm = Annotated[
    bool,
    Field(
        description="Must be true to request execution; false returns a dry-run preview."
    ),
]


class ValidationCheck(TypedDict):
    severity: Literal["error", "warning", "info"]
    code: str
    message: str


class MarketSnapshotResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str
    profile: Literal["minimal", "analysis", "execution"]
    as_of_ms: int
    data: dict[str, Any]
    warnings: list[str]
    raw: NotRequired[dict[str, Any]]


class TechnicalIndicatorsResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str
    period: str
    as_of_ms: int
    completed_candles: int
    omitted_incomplete_candles: int
    latest_completed_open_ms: int | None
    indicators: dict[str, dict[str, Any]]


class MarketWaitResult(TypedDict):
    status: Literal["triggered", "timed_out", "data_unavailable"]
    product: Literal["spot", "swap"]
    instrument: str
    match: Literal["any", "all"]
    started_at_ms: int
    finished_at_ms: int
    elapsed_ms: int
    polls: int
    observations: dict[str, str | None]
    matched_conditions: list[int]
    warnings: list[str]


class InstrumentRulesResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str | None
    matched: dict[str, Any] | None
    rules: dict[str, Any] | list[dict[str, Any]] | None
    available_count: int


class AccountSnapshotResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str | None
    margin_mode: Literal["isolated", "cross"] | None
    as_of_ms: int
    warnings: list[str]
    account_id: NotRequired[str]
    account_type: NotRequired[Any]
    balances: NotRequired[Any]
    positions: NotRequired[Any]
    open_orders: NotRequired[Any]
    api_status: NotRequired[Any]
    raw: NotRequired[dict[str, Any]]


class RiskSnapshotResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str
    market: dict[str, Any]
    account: dict[str, Any]
    warnings: list[str]


class TradeValidationResult(TypedDict):
    plan_id: str
    status: Literal["ready", "blocked"]
    product: Literal["spot", "swap"]
    instrument: str
    reference_price: str | None
    rules: dict[str, Any] | None
    checks: list[ValidationCheck]
    revalidate_before_execution: bool


class TradePlanResult(TradeValidationResult):
    request: dict[str, Any]
    market: MarketSnapshotResult


class ExecutionResult(TypedDict, total=False):
    executed: bool
    dry_run: bool
    reason: str
    tool: str
    request: dict[str, Any]
    ok: bool
    error: dict[str, Any]
    status: str
    code: int | str
    data: Any


class TradePreviewResult(TradePlanResult):
    execution: ExecutionResult


class TradeSubmissionResult(TypedDict):
    validation: TradePlanResult
    execution: ExecutionResult


class ReconcileTradeResult(TypedDict):
    product: Literal["spot", "swap"]
    instrument: str
    order: Any


class SpotMarginAction(BaseModel):
    """A bounded funding action for an HTX spot-margin account."""

    model_config = {"extra": "forbid"}

    action: Literal["transfer_in", "transfer_out", "borrow", "repay"] = Field(
        description="Funding action: transfer into/out of margin, borrow, or repay one loan."
    )
    margin_mode: Literal["isolated", "cross"] = Field(
        description="Spot-margin mode: isolated per symbol or cross across currencies."
    )
    currency: str = Field(
        min_length=1, description="Currency code, such as btc or usdt."
    )
    amount: DecimalAmount = Field(
        description="Exact positive funding amount; HTX spot-margin endpoints allow up to 3 decimal places."
    )
    instrument: str | None = Field(
        default=None,
        description="Required isolated-margin symbol, such as btcusdt; omit for cross margin.",
    )
    loan_order_id: str | None = Field(
        default=None,
        description="Required only for repay: the margin loan order ID to settle.",
    )

    @model_validator(mode="after")
    def validate_shape(self) -> SpotMarginAction:
        if self.margin_mode == "isolated" and not self.instrument:
            raise ValueError("instrument is required for isolated spot margin")
        if self.margin_mode == "cross" and self.instrument is not None:
            raise ValueError("instrument must be omitted for cross spot margin")
        if self.action == "repay" and not self.loan_order_id:
            raise ValueError("loan_order_id is required for repay")
        if self.action != "repay" and self.loan_order_id is not None:
            raise ValueError("loan_order_id is only valid for repay")
        if -self.amount.as_tuple().exponent > 3:
            raise ValueError("spot-margin amount supports at most 3 decimal places")
        return self


class SpotMarginPlanResult(TypedDict):
    status: Literal["ready", "blocked"]
    action: Literal["transfer_in", "transfer_out", "borrow", "repay"]
    margin_mode: Literal["isolated", "cross"]
    request: dict[str, Any]
    checks: list[ValidationCheck]
    warnings: list[str]


class SpotMarginExecutionResult(TypedDict):
    plan: SpotMarginPlanResult
    execution: ExecutionResult


class ProtectionSpec(BaseModel):
    """Exchange-side protection expressed with exact decimal values."""

    trigger_price: DecimalPrice
    order_price: DecimalPrice | None = Field(
        default=None,
        description="Optional execution price; omit to use HTX's optimal BBO execution.",
    )
    order_price_type: Literal["optimal_5", "optimal_10", "optimal_20"] = Field(
        default="optimal_5",
        description="HTX protection execution depth when order_price is omitted.",
    )


class TradeIntent(BaseModel):
    """Product-neutral intent used by validation, preview, and execution tools."""

    model_config = {"extra": "forbid"}

    product: Literal["spot", "swap"] = Field(
        description="Product family: spot or USDT-margined perpetual swap."
    )
    instrument: str = Field(
        min_length=1,
        description="Spot symbol such as 'btcusdt' or swap contract such as 'BTC-USDT'.",
    )
    account_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional spot account ID; omit to use HTX_SPOT_ACCOUNT_ID or resolve the unique working account.",
    )
    action: Literal["open", "close", "reduce"] = Field(
        default="open",
        description="Position intent. For swaps, reduce is a close-only order; spot uses open.",
    )
    side: Literal["buy", "sell"] = Field(
        description="Order side. For swaps, buy closes shorts and sell closes longs when action is close/reduce."
    )
    order_kind: Literal["market", "limit", "post_only", "ioc", "fok"] = Field(
        default="market",
        description="Execution style. Market uses the exchange's aggressive/BBO mode; limit styles require price when applicable.",
    )
    quantity: DecimalAmount
    price: DecimalPrice | None = Field(
        default=None,
        description="Limit price. Required for limit, post_only, IOC, and FOK styles; omit for market.",
    )
    margin_mode: Literal["isolated", "cross"] = Field(
        default="isolated",
        description="Swap margin mode; ignored for spot.",
    )
    leverage: int | None = Field(
        default=None,
        gt=0,
        description="Optional positive swap leverage multiplier; omit to use the account's current leverage.",
    )
    reduce_only: bool = Field(
        default=False,
        description="Swap-only close protection. When true, the order cannot increase exposure.",
    )
    position_side: Literal["long", "short", "both"] = Field(
        default="both",
        description="V5 swap position side. Use both for one-way mode; choose long or short for hedge mode.",
    )
    client_order_id: str | int | None = Field(
        default=None,
        description="Optional reconciliation ID: a 1-64 character identifier for spot; v5 swaps also accept a 1-64 character identifier or a positive 64-bit integer, while legacy swaps require the integer form.",
    )
    take_profit: ProtectionSpec | None = Field(
        default=None,
        description="Optional swap take-profit protection; validated against side and entry price.",
    )
    stop_loss: ProtectionSpec | None = Field(
        default=None,
        description="Optional swap stop-loss protection; validated against side and entry price.",
    )

    @model_validator(mode="after")
    def validate_client_order_id(self) -> TradeIntent:
        value = self.client_order_id
        if value is None:
            return self
        if self.product == "spot":
            if not isinstance(value, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,64}", value
            ):
                raise ValueError(
                    "spot client_order_id must contain 1-64 letters, digits, underscores, or hyphens"
                )
        elif isinstance(value, int) and not isinstance(value, bool):
            if not 1 <= value <= 9223372036854775807:
                raise ValueError(
                    "swap integer client_order_id must be from 1 through 9223372036854775807"
                )
        elif not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", value
        ):
            raise ValueError(
                "swap client_order_id must be an integer or a 1-64 character v5 identifier"
            )
        return self

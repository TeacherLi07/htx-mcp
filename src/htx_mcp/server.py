"""MCP server exposing HTX's official spot and USDT-margined swap APIs.

Every state-changing tool has two safety gates: the caller must pass
``confirm=true`` and the process must set ``HTX_ENABLE_TRADING=true``.
Otherwise it returns a dry-run preview and makes no mutation request.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from typing_extensions import NotRequired, Required, TypedDict

from . import __version__
from .client import (
    HtxApiError,
    HtxClient,
    HtxConfig,
    HtxError,  # noqa: F401 - re-exported for low-level tool modules and callers
    ensure_confirmation,
)
from .precision import decimal_to_text
from .semantic_tools import register_semantic_tools

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.getenv("HTX_LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s"
)
# httpx's INFO log contains the signed URL (including AccessKeyId and
# Signature). Keep transport diagnostics off by default so credentials never
# leak into the MCP host's stderr log.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _human_tool_title(function_name: str) -> str:
    """Create a readable client-facing title while keeping stable tool names."""

    title = function_name.replace("_", " ").title()
    return title.replace("Htx", "HTX").replace("Usdt", "USDT").replace("Api", "API")


def _enabled_toolsets() -> set[str] | None:
    """Return the toolset allow-list; production defaults to semantic read-only tools."""

    raw = os.getenv("HTX_TOOLSETS")
    if raw is None or not raw.strip():
        return {"analysis", "planning", "ops"}
    raw = raw.strip().lower()
    if raw == "all":
        return None
    values = {item.strip() for item in raw.split(",") if item.strip()}
    if "core" in values:
        values.update({"analysis", "planning"})
    if "trading" in values:
        values.update({"analysis", "planning", "execution"})
    return values


class HtxMcpServer(MCPServer):
    """Apply a human-readable title to every tool unless the caller supplies one."""

    def tool(self, *args: Any, **kwargs: Any):
        explicit_title = kwargs.pop("title", None)
        declared_toolsets = kwargs.pop("toolsets", None)

        def register(function: Any) -> Any:
            enabled = _enabled_toolsets()
            toolsets = set(declared_toolsets or ())
            if not toolsets:
                toolsets = {"advanced"}
                if function.__name__ == "htx_diagnose_private_access":
                    toolsets = {"ops"}
            if enabled is not None and not toolsets.intersection(enabled):
                return function
            return super(HtxMcpServer, self).tool(
                *args,
                title=explicit_title or _human_tool_title(function.__name__),
                **kwargs,
            )(function)

        return register


mcp = HtxMcpServer(
    name="htx-official-api",
    version=__version__,
    description="HTX official REST API tools for market data, account inspection, and guarded trading.",
    instructions=(
        "Use read-only tools to inspect live state and contract rules before trading. Prefer readable enum "
        "values such as all, open_long, greater_or_equal, and hedged when offered. Mutations are dry-run "
        "unless confirm=true and HTX_ENABLE_TRADING=true are both present. An order acknowledgement is "
        "not a fill; query order and position status after every mutation."
    ),
)
client = HtxClient(HtxConfig.from_env())

READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)

# The MCP Python SDK publishes function annotations as JSON Schema.  Keep the
# common HTX vocabulary in reusable aliases so every tool exposes the same
# examples, units, and safety hints instead of relying on a model to infer
# them from a parameter name.
SpotSymbol = Annotated[
    str,
    Field(
        description="HTX spot symbol, lowercase concatenated currencies, for example 'btcusdt'."
    ),
]
ContractCode = Annotated[
    str,
    Field(description="HTX USDT-margined contract code, normally 'BTC-USDT'."),
]
AccountId = Annotated[
    str,
    Field(
        description="HTX spot account ID returned by spot_get_accounts; it is not the API key."
    ),
]
ExchangeOrderId = Annotated[
    str,
    Field(description="HTX exchange order ID as a string."),
]
ClientOrderId = Annotated[
    str,
    Field(
        description="Client-generated order ID used to query or cancel the order; keep it unique."
    ),
]
FuturesClientOrderId = Annotated[
    int,
    Field(
        gt=0,
        le=9223372036854775807,
        description="Numeric USDT-swap client order ID; use a unique integer from 1 through 9223372036854775807.",
    ),
]
SpotClientOrderId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Spot client order ID used to query or cancel the order; use 1-64 letters, digits, underscores, or hyphens and keep it unique.",
    ),
]
CurrencyCode = Annotated[
    str,
    Field(
        description="Currency code such as 'btc' or 'usdt'; HTX accepts case-insensitive input."
    ),
]
MillisecondTimestamp = Annotated[
    int,
    Field(
        description="Unix timestamp in milliseconds, for example 1704067200000; do not use seconds."
    ),
]
CandlePeriod = Annotated[
    Literal[
        "1min", "5min", "15min", "30min", "60min", "4hour", "1day", "1week", "1mon"
    ],
    Field(
        description="Candle interval. Use the shortest interval that covers the requested analysis."
    ),
]
PageDirection = Annotated[
    Literal["next", "prev"],
    Field(
        description="Pagination direction: 'next' moves forward from the cursor; 'prev' moves backward."
    ),
]
V3PageSize = Annotated[
    int,
    Field(
        ge=1,
        le=50,
        description="Number of records returned by a v3 history query; HTX accepts 1-50.",
    ),
]
MarginMode = Annotated[
    Literal["isolated", "cross"],
    Field(
        description="Swap margin mode. 'isolated' uses the contract account; 'cross' uses the USDT account."
    ),
]
FuturesDirection = Annotated[
    Literal["buy", "sell"],
    Field(
        description="Order direction: buy opens/short-closes; sell opens/long-closes, subject to offset."
    ),
]
FuturesOffset = Annotated[
    Literal["open", "close", "both"],
    Field(
        description="Position action: 'open', 'close', or 'both'. In hedge mode HTX requires the explicit side."
    ),
]
Confirm = Annotated[
    bool,
    Field(
        description="Must be true to request execution; false returns a dry-run preview. The server must also enable HTX_ENABLE_TRADING."
    ),
]
SpotDepthType = Annotated[
    Literal["step0", "step1", "step2", "step3", "step4", "step5"],
    Field(
        description="HTX order-book aggregation level; step0 is the least aggregated view."
    ),
]
FuturesDepthType = Annotated[
    Literal[
        "step0",
        "step1",
        "step2",
        "step3",
        "step4",
        "step5",
        "step6",
        "step7",
        "step8",
        "step9",
        "step10",
        "step11",
        "step12",
        "step13",
        "step14",
        "step15",
        "step16",
        "step17",
        "step18",
        "step19",
    ],
    Field(
        description="HTX swap order-book aggregation level, from step0 through step19."
    ),
]
SpotOrderType = Annotated[
    Literal[
        "buy-market",
        "sell-market",
        "buy-limit",
        "sell-limit",
        "buy-ioc",
        "sell-ioc",
        "buy-limit-maker",
        "sell-limit-maker",
        "buy-stop-limit",
        "sell-stop-limit",
        "buy-limit-fok",
        "sell-limit-fok",
        "buy-stop-limit-fok",
        "sell-stop-limit-fok",
    ],
    Field(
        description=(
            "Spot order type. Market orders omit price; limit, IOC, FOK, maker, and stop-limit "
            "orders require the fields described by spot_place_order."
        )
    ),
]
FuturesOrderPriceType = Annotated[
    Literal[
        "limit",
        "opponent",
        "post_only",
        "optimal_5",
        "optimal_10",
        "optimal_20",
        "lightning",
        "fok",
        "ioc",
        "opponent_ioc",
        "lightning_ioc",
        "optimal_5_ioc",
        "optimal_10_ioc",
        "optimal_20_ioc",
        "opponent_fok",
        "lightning_fok",
        "optimal_5_fok",
        "optimal_10_fok",
        "optimal_20_fok",
    ],
    Field(
        description=(
            "Swap order price type: limit/post_only need price; opponent uses BBO; optimal_N uses "
            "the top N BBO levels; IOC/FOK variants add time-in-force."
        )
    ),
]
TradeTypeInput = Annotated[
    Literal[
        "all",
        "open_long",
        "open_short",
        "close_short",
        "close_long",
        "liquidate_long",
        "liquidate_short",
        "buy",
        "sell",
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        17,
        18,
    ],
    Field(
        description=(
            "Trade filter. Prefer readable values: all, open_long, open_short, close_short, close_long, "
            "liquidate_long, liquidate_short, buy, or sell. Legacy numeric HTX codes are also accepted."
        )
    ),
]
OrderTradeTypeInput = Annotated[
    Literal[
        "all",
        "open_long",
        "close_short",
        "open_short",
        "close_long",
        "buy",
        "sell",
        0,
        1,
        2,
        3,
        4,
        17,
        18,
    ],
    Field(
        description=(
            "Order filter. Prefer all, open_long, close_short, open_short, close_long, buy, or sell; "
            "legacy numeric HTX codes are also accepted."
        )
    ),
]
TriggerCondition = Annotated[
    Literal["greater_or_equal", "less_or_equal", "ge", "le"],
    Field(
        description="Trigger comparison: greater_or_equal means market price >= trigger_price; less_or_equal means <=."
    ),
]
PositionMode = Annotated[
    Literal["one_way", "hedged", "single_side", "dual_side"],
    Field(
        description="Position mode: one_way keeps one net position; hedged allows separate long and short positions."
    ),
]
StopOperator = Annotated[
    Literal["greater_or_equal", "less_or_equal", "gte", "lte"],
    Field(
        description="Spot stop comparison: greater_or_equal triggers at/above stop_price; less_or_equal triggers at/below."
    ),
]


class FuturesBatchOrder(TypedDict, total=False):
    """One item accepted by HTX's swap batch-order endpoint."""

    contract_code: Required[ContractCode]
    volume: Required[
        Annotated[
            Decimal,
            Field(
                gt=0, description="Order volume in contract units; must be positive."
            ),
        ]
    ]
    direction: Required[FuturesDirection]
    order_price_type: Required[FuturesOrderPriceType]
    price: NotRequired[
        Annotated[
            Decimal,
            Field(
                gt=0,
                description="Limit price; required for limit/post_only/IOC/FOK variants.",
            ),
        ]
    ]
    offset: NotRequired[FuturesOffset]
    lever_rate: NotRequired[
        Annotated[
            int, Field(gt=0, description="Positive leverage multiplier, for example 5.")
        ]
    ]
    reduce_only: NotRequired[
        Annotated[
            bool,
            Field(
                description="When true, the order may only reduce an existing position."
            ),
        ]
    ]
    client_order_id: NotRequired[FuturesClientOrderId]
    tp_trigger_price: NotRequired[
        Annotated[Decimal, Field(gt=0, description="Take-profit trigger price.")]
    ]
    tp_order_price: NotRequired[
        Annotated[Decimal, Field(gt=0, description="Take-profit execution price.")]
    ]
    tp_order_price_type: NotRequired[
        Annotated[
            Literal["optimal_5", "optimal_10", "optimal_20"],
            Field(
                description="Take-profit execution depth: optimal_5, optimal_10, or optimal_20."
            ),
        ]
    ]
    sl_trigger_price: NotRequired[
        Annotated[Decimal, Field(gt=0, description="Stop-loss trigger price.")]
    ]
    sl_order_price: NotRequired[
        Annotated[Decimal, Field(gt=0, description="Stop-loss execution price.")]
    ]
    sl_order_price_type: NotRequired[
        Annotated[
            Literal["optimal_5", "optimal_10", "optimal_20"],
            Field(
                description="Stop-loss execution depth: optimal_5, optimal_10, or optimal_20."
            ),
        ]
    ]


def _q(**values: Any) -> dict[str, Any]:
    """Drop unset query fields while preserving valid falsey values."""

    return {key: value for key, value in values.items() if value is not None}


def _symbol(symbol: str) -> str:
    value = symbol.strip().lower()
    if not value or any(ch.isspace() for ch in value):
        raise ToolError("symbol must be a non-empty symbol such as btcusdt")
    return value


def _contract(contract_code: str) -> str:
    value = contract_code.strip().upper()
    if not value or any(ch.isspace() for ch in value):
        raise ToolError("contract_code must be a non-empty code such as BTC-USDT")
    return value


def _page(page_index: int, page_size: int) -> tuple[int, int]:
    if page_index < 1:
        raise ToolError("page_index must be >= 1")
    if not 1 <= page_size <= 50:
        raise ToolError("page_size must be between 1 and 50")
    return page_index, page_size


def _text(value: str | None, name: str) -> str:
    if value is None:
        raise ToolError(f"{name} is required")
    result = value.strip()
    if not result or any(char.isspace() for char in result):
        raise ToolError(f"{name} must be a non-empty value without whitespace")
    return result


def _positive_number(value: Decimal | float | int, name: str) -> Decimal:
    """Validate and normalize a monetary/quantity value without binary floats."""

    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolError(f"{name} must be a valid decimal number") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ToolError(f"{name} must be a finite decimal greater than zero")
    return decimal_value


def _decimal_text(value: Decimal | float | int) -> str:
    """Return an exact fixed-point representation suitable for HTX JSON bodies."""

    return decimal_to_text(_positive_number(value, "decimal value"))


def _decimal_or_none(value: Any) -> str | None:
    return _decimal_text(value) if value is not None else None


def _positive_integer(value: int, name: str) -> int:
    if value <= 0:
        raise ToolError(f"{name} must be greater than zero")
    return value


def _validate_time_range(
    start_time: int | None,
    end_time: int | None,
    *,
    max_window_ms: int | None = None,
    max_age_ms: int | None = 90 * 24 * 60 * 60 * 1000,
) -> None:
    now = int(time.time() * 1000)
    for name, value in (("start_time", start_time), ("end_time", end_time)):
        if value is not None:
            if value <= 0:
                raise ToolError(f"{name} must be a positive millisecond timestamp")
            if value > now:
                raise ToolError(f"{name} must not be in the future")
            if max_age_ms is not None and now - value > max_age_ms:
                max_age_days = max_age_ms // (24 * 60 * 60 * 1000)
                raise ToolError(f"{name} must be within the last {max_age_days} days")
    if start_time is not None and end_time is not None:
        if start_time > end_time:
            raise ToolError("start_time must be earlier than or equal to end_time")
        if max_window_ms is not None and end_time - start_time > max_window_ms:
            raise ToolError("the requested time window is too large")


def _create_date(create_date: int) -> int:
    if not 1 <= create_date <= 90:
        raise ToolError("create_date must be between 1 and 90 days")
    return create_date


def _status_list(status: str, *, allowed: set[str]) -> str:
    value = _text(status, "status")
    normalized = [item.strip() for item in value.split(",") if item.strip()]
    statuses = set(normalized)
    if not statuses or any(item not in allowed for item in statuses):
        allowed_text = ", ".join(sorted(allowed))
        raise ToolError(f"status must contain only: {allowed_text}")
    return ",".join(normalized)


FUTURES_TRADE_TYPES = {0, 1, 2, 3, 4, 5, 6, 17, 18}
FUTURES_ORDER_TRADE_TYPES = {0, 1, 2, 3, 4, 17, 18}
FUTURES_ORDER_STATUSES = {"0", "3", "4", "5", "6", "7"}
TRIGGER_ORDER_STATUSES = {"0", "4", "5", "6"}
FINANCIAL_RECORD_TYPES = {
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "19",
    "26",
    "28",
    "29",
    "30",
    "31",
    "34",
    "35",
    "36",
    "37",
    "38",
    "39",
    "46",
    "47",
}

TRADE_TYPE_NAMES = {
    "all": 0,
    "open_long": 1,
    "open_short": 2,
    "close_short": 3,
    "close_long": 4,
    "liquidate_long": 5,
    "liquidate_short": 6,
    "buy": 17,
    "sell": 18,
}


def _trade_type(value: str | int, *, order_filter: bool = False) -> int:
    """Convert readable trade filters to the numeric values required by HTX."""

    normalized = TRADE_TYPE_NAMES.get(value, value) if isinstance(value, str) else value
    allowed = FUTURES_ORDER_TRADE_TYPES if order_filter else FUTURES_TRADE_TYPES
    if normalized not in allowed:
        allowed_text = ", ".join(str(item) for item in sorted(allowed))
        raise ToolError(
            f"trade_type must be a supported readable value or HTX code: {allowed_text}"
        )
    return int(normalized)


def _trigger_condition(value: str) -> str:
    """Convert readable trigger conditions to HTX's ``ge``/``le`` values."""

    return {"greater_or_equal": "ge", "less_or_equal": "le"}.get(value, value)


def _position_mode(value: str) -> str:
    """Convert readable position modes to HTX's ``single_side``/``dual_side`` values."""

    return {"one_way": "single_side", "hedged": "dual_side"}.get(value, value)


def _stop_operator(value: str) -> str:
    """Convert readable spot stop operators to HTX's ``gte``/``lte`` values."""

    return {"greater_or_equal": "gte", "less_or_equal": "lte"}.get(value, value)


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


async def _public_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await client.request(
        "POST", path, body=body or {}, private=False, base_url=_base_url_for(path)
    )


async def _private_get(path: str, **query: Any) -> dict[str, Any]:
    return await client.request(
        "GET", path, query=_q(**query), private=True, base_url=_base_url_for(path)
    )


async def _private_post(
    path: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    return await client.request(
        "POST", path, body=body or {}, private=True, base_url=_base_url_for(path)
    )


def _wire_body(value: Any) -> Any:
    """Make request previews and HTX bodies deterministic and JSON-safe."""

    if isinstance(value, Decimal):
        return decimal_to_text(value)
    if isinstance(value, dict):
        return {key: _wire_body(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_wire_body(item) for item in value]
    return value


async def _mutation(
    tool_name: str,
    path: str,
    body: dict[str, Any] | None,
    confirm: bool,
    *,
    mode: str = "POST",
) -> dict[str, Any]:
    wire_body = _wire_body(body or {})
    request = {"method": mode, "path": path, "body": wire_body}
    preview = ensure_confirmation(
        client, tool_name=tool_name, confirm=confirm, request=request
    )
    if preview is not None:
        return preview
    return await client.request(
        mode,
        path,
        body=wire_body,
        private=True,
        base_url=_base_url_for(path),
    )


async def _resolve_spot_account_id(account_id: str | None) -> str:
    """Use the configured account, explicit account, or authenticated spot account."""

    if account_id is not None:
        return _text(account_id, "account_id")
    if client.config.spot_account_id:
        return client.config.spot_account_id

    accounts = await _private_get("/v1/account/accounts")
    candidates = [
        item
        for item in accounts.get("data", [])
        if item.get("type") == "spot" and item.get("state") in {None, "working"}
    ]
    if len(candidates) != 1 or not candidates[0].get("id"):
        raise ToolError(
            "No unique spot account was found. Set HTX_SPOT_ACCOUNT_ID or pass account_id explicitly."
        )
    return str(candidates[0]["id"])


def _diagnostic_error(error: Exception) -> dict[str, Any]:
    """Expose actionable HTX errors without returning secrets or signed URLs."""

    report: dict[str, Any] = {
        "ok": False,
        "error_type": type(error).__name__,
        "message": str(error),
    }
    if isinstance(error, HtxApiError):
        report["http_status"] = error.status_code
        if isinstance(error.payload, dict):
            report["htx_error_code"] = (
                error.payload.get("err-code")
                or error.payload.get("err_code")
                or error.payload.get("code")
            )
            report["htx_error_message"] = (
                error.payload.get("err-msg")
                or error.payload.get("err_msg")
                or error.payload.get("message")
                or error.payload.get("msg")
            )
    return report


def _validate_spot_order(
    order_type: str,
    amount: Decimal,
    price: Decimal | None,
    stop_price: Decimal | None,
    operator: str | None,
) -> None:
    allowed = {
        "buy-market",
        "sell-market",
        "buy-limit",
        "sell-limit",
        "buy-ioc",
        "sell-ioc",
        "buy-limit-maker",
        "sell-limit-maker",
        "buy-stop-limit",
        "sell-stop-limit",
        "buy-limit-fok",
        "sell-limit-fok",
        "buy-stop-limit-fok",
        "sell-stop-limit-fok",
    }
    if order_type not in allowed:
        raise ToolError(f"unsupported spot order type: {order_type}")
    _positive_number(amount, "amount")
    if price is not None:
        _positive_number(price, "price")
    if "market" not in order_type and price is None:
        raise ToolError("price is required for non-market spot orders")
    is_stop_order = "stop" in order_type
    if is_stop_order and stop_price is None:
        raise ToolError("stop_price is required for stop-limit spot orders")
    if is_stop_order and operator is None:
        raise ToolError("operator is required for stop-limit spot orders")
    if not is_stop_order and (stop_price is not None or operator is not None):
        raise ToolError(
            "stop_price and operator are only valid for stop-limit spot orders"
        )
    if stop_price is not None:
        _positive_number(stop_price, "stop_price")


def _validate_futures_order(
    *,
    volume: Decimal,
    direction: str,
    offset: str | None,
    order_price_type: str,
    price: Decimal | None,
    lever_rate: int | None,
    tp_trigger_price: Decimal | None,
    sl_trigger_price: Decimal | None,
) -> None:
    _positive_number(volume, "volume")
    if direction not in {"buy", "sell"}:
        raise ToolError("direction must be buy or sell")
    if offset is not None and offset not in {"open", "close", "both"}:
        raise ToolError("offset must be open, close, or both")
    allowed_price_types = {
        "limit",
        "opponent",
        "post_only",
        "optimal_5",
        "optimal_10",
        "optimal_20",
        "lightning",
        "fok",
        "ioc",
        "opponent_ioc",
        "lightning_ioc",
        "optimal_5_ioc",
        "optimal_10_ioc",
        "optimal_20_ioc",
        "opponent_fok",
        "lightning_fok",
        "optimal_5_fok",
        "optimal_10_fok",
        "optimal_20_fok",
    }
    if order_price_type not in allowed_price_types:
        raise ToolError(f"unsupported futures order_price_type: {order_price_type}")
    if order_price_type in {"limit", "post_only", "fok", "ioc"} and price is None:
        raise ToolError(f"price is required for order_price_type={order_price_type}")
    if price is not None:
        _positive_number(price, "price")
    if lever_rate is not None and lever_rate <= 0:
        raise ToolError("lever_rate must be positive")
    if tp_trigger_price is not None and tp_trigger_price <= 0:
        raise ToolError("tp_trigger_price must be positive")
    if sl_trigger_price is not None and sl_trigger_price <= 0:
        raise ToolError("sl_trigger_price must be positive")


@mcp.resource("htx://configuration", name="configuration", mime_type="application/json")
def configuration_resource() -> str:
    """Expose non-secret HTX MCP configuration and safety state as JSON.

    The resource reports API hosts, credential presence, trading enablement, account-ID configuration, supported products, and the dry-run safety gates; it never contains API secrets.
    """

    return _json(
        {
            "server": "htx-official-api",
            "api_base_url": client.config.base_url,
            "futures_api_base_url": client.config.futures_base_url,
            "credentials_configured": client.credentials_configured,
            "trading_enabled": client.config.enable_trading,
            "toolsets": os.getenv("HTX_TOOLSETS")
            or "analysis,planning,ops (semantic default)",
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
    product: Annotated[
        Literal["spot", "swap"],
        Field(description="Product family: spot or USDT-margined perpetual swap."),
    ],
    instrument: Annotated[
        str, Field(description="Spot symbol or USDT-swap contract code to review.")
    ],
    side: Annotated[
        Literal["buy", "sell"], Field(description="Order side: buy or sell.")
    ],
    quantity: Annotated[
        str,
        Field(
            description="Exact decimal spot amount or swap contract volume, expressed as a string."
        ),
    ],
    action: Annotated[
        Literal["open", "close", "reduce"],
        Field(description="Position intent; spot uses open."),
    ] = "open",
    order_kind: Annotated[
        Literal["market", "limit", "post_only", "ioc", "fok"],
        Field(description="Execution style; non-market styles require price."),
    ] = "market",
    price: Annotated[
        str | None,
        Field(description="Exact decimal limit price string; omit for a market order."),
    ] = None,
    margin_mode: Annotated[
        Literal["isolated", "cross"],
        Field(description="Swap margin mode; ignored for spot."),
    ] = "isolated",
    stop_loss: Annotated[
        str | None,
        Field(description="Optional exact decimal stop-loss trigger price string."),
    ] = None,
    take_profit: Annotated[
        str | None,
        Field(description="Optional exact decimal take-profit trigger price string."),
    ] = None,
) -> str:
    """Create a compact HTX trade-preflight checklist before enabling a mutation tool.

    This prompt does not place an order; it asks the model to verify contract rules, balance, leverage, existing orders/positions, and protection direction first.
    """

    return (
        "Prepare an HTX trade preflight review only; do not call execution tools or submit an order.\n"
        f"- Product: {product}\n- Instrument: {instrument}\n- Action: {action}\n"
        f"- Side: {side}\n- Order kind: {order_kind}\n- Quantity: {quantity}\n"
        f"- Price: {price or 'none'}\n- Margin mode: {margin_mode}\n"
        f"- Stop loss trigger: {stop_loss or 'none'}\n- Take profit trigger: {take_profit or 'none'}\n"
        "1. Call htx_get_instrument_rules and htx_get_risk_snapshot for live precision, minimums, "
        "price limits, balance, leverage, positions, and open orders.\n"
        "2. Convert the fields above into one TradeIntent, preserving decimal values as strings, and call "
        "htx_validate_trade_intent. If its status is ready, call htx_preview_trade.\n"
        "3. Report blocking checks, warnings, normalized request, and whether stop/target direction is valid. "
        "Do not set confirm=true; leave execution to a separate explicit user request."
    )


# ---------------------------------------------------------------------------
def _swap_endpoint(
    stem: str,
    margin_mode: Literal["isolated", "cross"],
    *,
    version: Literal["v1", "v3"] = "v1",
) -> str:
    prefix = "swap_" if margin_mode == "isolated" else "swap_cross_"
    return f"/linear-swap-api/{version}/{prefix}{stem}"


def _swap_body(contract_code: str | None = None, **values: Any) -> dict[str, Any]:
    return _q(
        contract_code=_contract(contract_code) if contract_code is not None else None,
        **values,
    )


def _v3_margin_account(
    margin_mode: Literal["isolated", "cross"],
    contract_code: str | None,
    margin_account: str | None,
) -> str:
    if margin_account is not None:
        account = _text(margin_account, "margin_account").upper()
    elif margin_mode == "cross":
        account = "USDT"
    elif contract_code is not None:
        account = _contract(contract_code)
    else:
        raise ToolError(
            "margin_account or contract_code is required for the v3 financial-record query"
        )
    if margin_mode == "cross" and account != "USDT":
        raise ToolError("cross-margin financial records require margin_account=USDT")
    return account


# ---------------------------------------------------------------------------
from . import spot_tools as _spot_tools  # noqa: E402
from . import swap_account_tools as _swap_account_tools  # noqa: E402
from . import swap_market_tools as _swap_market_tools  # noqa: E402
from . import swap_trading_tools as _swap_trading_tools  # noqa: E402

for _tool_module in (
    _spot_tools,
    _swap_market_tools,
    _swap_account_tools,
    _swap_trading_tools,
):
    globals().update(
        {name: getattr(_tool_module, name) for name in _tool_module.__all__}
    )
register_semantic_tools(mcp, sys.modules[__name__])


def main() -> None:
    """Run the MCP server; stdio is the default transport for desktop hosts."""

    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise SystemExit("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    logger.info("HTX MCP server starting on %s transport", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()

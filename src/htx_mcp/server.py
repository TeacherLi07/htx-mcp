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

from .client import (
    HtxApiError,
    HtxClient,
    HtxConfig,
    HtxError,
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
    version="0.1.0",
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
    client_order_id: NotRequired[
        Annotated[
            str,
            Field(description="Optional unique client order ID for reconciliation."),
        ]
    ]
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
    symbol_or_contract: Annotated[
        str, Field(description="Spot symbol or USDT-swap contract code to review.")
    ],
    side: Annotated[
        str,
        Field(
            description="Trade side and intended action, for example buy/open or sell/close."
        ),
    ],
    entry_price: Annotated[
        str,
        Field(
            description="Planned entry price, including units and precision if known."
        ),
    ],
    stop_loss: Annotated[
        str,
        Field(description="Planned stop-loss trigger and execution price, or 'none'."),
    ],
    take_profit: Annotated[
        str,
        Field(
            description="Planned take-profit trigger and execution price, or 'none'."
        ),
    ],
    quantity: Annotated[
        str,
        Field(description="Planned spot amount or contract volume, including units."),
    ],
) -> str:
    """Create a compact HTX trade-preflight checklist before enabling a mutation tool.

    This prompt does not place an order; it asks the model to verify contract rules, balance, leverage, existing orders/positions, and protection direction first.
    """

    return (
        "Before submitting this HTX trade, verify live metadata and account state:\n"
        f"- Instrument: {symbol_or_contract}\n- Side: {side}\n- Entry: {entry_price}\n"
        f"- Stop loss: {stop_loss}\n- Take profit: {take_profit}\n- Quantity: {quantity}\n"
        "- Confirm contract precision/minimum, available balance, leverage, existing positions/orders, "
        "and stop/target direction. Use confirm=true only after this review."
    )


# ---------------------------------------------------------------------------
# Spot market and metadata
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def spot_get_ticker(symbol: SpotSymbol) -> dict[str, Any]:
    """Read the latest merged ticker for one HTX spot symbol.

    Use this for the current last price, best bid/ask, 24-hour volume, and price statistics.
    Returns the raw HTX JSON envelope; call spot_get_symbols first when precision or minimums matter.
    """

    return await _public("/market/detail/merged", symbol=_symbol(symbol))


@mcp.tool(annotations=READ)
async def spot_get_tickers() -> dict[str, Any]:
    """Read merged tickers for every currently supported HTX spot symbol.

    Use this for a market-wide snapshot. The response can be large; use spot_get_ticker for one symbol.
    """

    return await _public("/market/tickers")


@mcp.tool(annotations=READ)
async def spot_get_klines(
    symbol: SpotSymbol,
    period: CandlePeriod = "1day",
    size: Annotated[
        int,
        Field(
            ge=1, le=2000, description="Number of candles to return; HTX allows 1-2000."
        ),
    ] = 100,
    from_time: MillisecondTimestamp | None = None,
    to_time: MillisecondTimestamp | None = None,
) -> dict[str, Any]:
    """Read historical OHLCV candlesticks for one HTX spot symbol.

    ``from_time`` and ``to_time`` are optional Unix milliseconds; ``size`` is 1-2000.
    The response is HTX's raw kline envelope, ordered according to the exchange API.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    _validate_time_range(from_time, to_time, max_age_ms=None)
    return await _public(
        "/market/history/kline",
        symbol=_symbol(symbol),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@mcp.tool(annotations=READ)
async def spot_get_depth(
    symbol: SpotSymbol,
    depth_type: SpotDepthType = "step0",
    depth: Annotated[
        Literal[5, 10, 20],
        Field(description="Number of bid and ask levels to return: 5, 10, or 20."),
    ] = 20,
) -> dict[str, Any]:
    """Read the current spot order book for one symbol.

    ``depth_type`` controls price aggregation and ``depth`` controls the number of bid/ask levels.
    This is public market data and does not require API credentials.
    """

    return await _public(
        "/market/depth", symbol=_symbol(symbol), type=depth_type, depth=depth
    )


@mcp.tool(annotations=READ)
async def spot_get_recent_trades(
    symbol: SpotSymbol,
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
    return await _public("/market/history/trade", symbol=_symbol(symbol), size=size)


@mcp.tool(annotations=READ)
async def spot_get_symbols() -> dict[str, Any]:
    """Read HTX spot symbol metadata and trading rules.

    Use the response to check price/amount precision, minimum order sizes, market limits, and whether a symbol is trading before placing an order.
    """

    return await _public("/v1/common/symbols")


@mcp.tool(annotations=READ)
async def spot_get_currencies() -> dict[str, Any]:
    """Read HTX spot currency settings.

    Use this for currency precision, withdrawal/deposit settings, and currency status; chain-level details are available from spot_get_currency_reference.
    """

    return await _public("/v1/common/currencys")


@mcp.tool(annotations=READ)
async def spot_get_currency_reference(
    currency: Annotated[
        CurrencyCode | None,
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

    return await _public(
        "/v2/reference/currencies",
        currency=_text(currency, "currency") if currency is not None else None,
        authorizedUser=authorized_user,
    )


@mcp.tool(annotations=READ)
async def spot_get_market_status() -> dict[str, Any]:
    """Read the current HTX spot market service status.

    Use this before interpreting a market-data gap or submitting a time-sensitive order.
    """

    return await _public("/v2/market-status")


@mcp.tool(annotations=READ)
async def spot_get_server_timestamp() -> dict[str, Any]:
    """Read the current HTX server timestamp in milliseconds.

    Use this to compare clock skew before time-window queries or signed private requests.
    """

    return await _public("/v1/common/timestamp")


# ---------------------------------------------------------------------------
# Spot account and read-only order inspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def spot_get_accounts() -> dict[str, Any]:
    """List the authenticated user's HTX account IDs and account types.

    Use the returned working ``spot`` account ID for balance and order tools when HTX_SPOT_ACCOUNT_ID is not configured.
    Requires read permission and API credentials.
    """

    return await _private_get("/v1/account/accounts")


@mcp.tool(annotations=READ)
async def htx_diagnose_private_access() -> dict[str, Any]:
    """Diagnose authenticated access to HTX spot and USDT-swap private APIs without trading.

    The result reports credential presence, configured hosts, each check's success, and sanitized HTX error codes/messages. It never returns a secret, signature, or signed URL.
    """

    checks: dict[str, Any] = {
        "credentials_configured": client.credentials_configured,
        "spot_host": client.config.base_url,
        "futures_host": client.config.futures_base_url,
        "trading_enabled": client.config.enable_trading,
    }
    for name, path in {
        "spot_accounts": "/v1/account/accounts",
        "futures_api_trading_status": "/linear-swap-api/v1/swap_api_trading_status",
    }.items():
        try:
            response = await _private_get(path)
            checks[name] = {
                "ok": True,
                "status": response.get("status"),
                "code": response.get("code"),
            }
        except HtxError as error:
            checks[name] = _diagnostic_error(error)
    return checks


@mcp.tool(annotations=READ)
async def spot_get_account_balance(
    account_id: AccountId | None = None,
) -> dict[str, Any]:
    """Read balances for one authenticated HTX spot account.

    Pass ``account_id`` explicitly, or omit it to use HTX_SPOT_ACCOUNT_ID or resolve the unique working spot account automatically.
    """

    resolved = await _resolve_spot_account_id(account_id)
    return await _private_get(f"/v1/account/accounts/{resolved}/balance")


@mcp.tool(annotations=READ)
async def spot_get_open_orders(
    account_id: AccountId | None = None,
    symbol: SpotSymbol | None = None,
    size: Annotated[
        int,
        Field(
            ge=1,
            le=1000,
            description="Maximum open orders to return; HTX allows 1-1000.",
        ),
    ] = 100,
    direct: PageDirection | None = None,
    from_order_id: ExchangeOrderId | None = None,
) -> dict[str, Any]:
    """Read currently open HTX spot orders.

    Optionally filter by symbol and use ``direct`` plus ``from_order_id`` to paginate. This is read-only and does not cancel anything.
    """

    if not 1 <= size <= 1000:
        raise ToolError("size must be between 1 and 1000")
    if from_order_id is not None:
        _text(from_order_id, "from_order_id")
    resolved = await _resolve_spot_account_id(account_id)
    return await _private_get(
        "/v1/order/openOrders",
        **{
            "account-id": resolved,
            "symbol": _symbol(symbol) if symbol is not None else None,
            "size": size,
            "direct": direct,
            "from": from_order_id,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_order(order_id: ExchangeOrderId) -> dict[str, Any]:
    """Read one HTX spot order by its exchange order ID.

    The response contains the order status and fill fields; an accepted order is not necessarily filled.
    """

    return await _private_get(f"/v1/order/orders/{_text(order_id, 'order_id')}")


@mcp.tool(annotations=READ)
async def spot_get_order_by_client_id(client_order_id: ClientOrderId) -> dict[str, Any]:
    """Read one HTX spot order by the client-generated order ID.

    Use this after a timeout when the exchange order ID was not received. Keep client IDs unique and reuse the same ID for reconciliation.
    """

    return await _private_get(
        "/v1/order/orders/getClientOrder",
        clientOrderId=_text(client_order_id, "client_order_id"),
    )


@mcp.tool(annotations=READ)
async def spot_get_match_results(
    symbol: SpotSymbol,
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
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    from_match_id: Annotated[
        str | None,
        Field(
            description="Pagination cursor: the internal match-result id, not trade-id."
        ),
    ] = None,
    direct: PageDirection = "next",
) -> dict[str, Any]:
    """Read filled or partially filled HTX spot match results for one symbol.

    The query window is at most 48 hours and can be shifted within the supported history period; pagination uses the internal match-result ID, not trade-id.
    """

    if not 1 <= size <= 500:
        raise ToolError("size must be between 1 and 500")
    _validate_time_range(
        start_time,
        end_time,
        max_window_ms=48 * 60 * 60 * 1000,
        max_age_ms=180 * 24 * 60 * 60 * 1000,
    )
    if from_match_id is not None:
        _text(from_match_id, "from_match_id")
    return await _private_get(
        "/v1/order/matchresults",
        **{
            "symbol": _symbol(symbol),
            "types": types,
            "size": size,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_match_id,
            "direct": direct,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_order_match_results(order_id: ExchangeOrderId) -> dict[str, Any]:
    """Read all HTX spot fills associated with one exchange order ID.

    Use this to confirm partial fills, fill price, fees, and filled amount after querying the order.
    """

    return await _private_get(
        f"/v1/order/orders/{_text(order_id, 'order_id')}/matchresults"
    )


@mcp.tool(annotations=READ)
async def spot_get_history_orders(
    symbol: SpotSymbol,
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
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    from_order_id: ExchangeOrderId | None = None,
    direct: PageDirection = "next",
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
    states = _status_list(
        states,
        allowed={
            "submitted",
            "partial-filled",
            "filled",
            "canceled",
            "partial-canceled",
        },
    )
    _validate_time_range(
        start_time,
        end_time,
        max_window_ms=48 * 60 * 60 * 1000,
        max_age_ms=180 * 24 * 60 * 60 * 1000,
    )
    if from_order_id is not None:
        _text(from_order_id, "from_order_id")
    return await _private_get(
        "/v1/order/orders",
        **{
            "symbol": _symbol(symbol),
            "states": states,
            "types": types,
            "start-time": start_time,
            "end-time": end_time,
            "from": from_order_id,
            "direct": direct,
            "size": size,
        },
    )


@mcp.tool(annotations=READ)
async def spot_get_recent_history_orders(
    symbol: SpotSymbol | None = None,
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    direct: PageDirection = "next",
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
    _validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    return await _private_get(
        "/v1/order/history",
        **{
            "symbol": _symbol(symbol) if symbol is not None else None,
            "start-time": start_time,
            "end-time": end_time,
            "direct": direct,
            "size": size,
        },
    )


@mcp.tool(annotations=WRITE)
async def spot_dead_man_switch(
    timeout_seconds: Annotated[
        int,
        Field(
            ge=0,
            description="Timeout in seconds: 0 disables the switch; 5 or more arms it.",
        ),
    ],
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Arm or disarm HTX's spot dead-man switch.

    ``timeout_seconds=0`` disables it; a value of 5 or more arms automatic cancellation of open spot orders after the timeout. This is a state-changing tool and remains a dry run unless both confirmation gates pass.
    """

    if timeout_seconds != 0 and timeout_seconds < 5:
        raise ToolError("timeout_seconds must be 0 or at least 5")
    return await _mutation(
        "spot_dead_man_switch",
        "/v2/algo-orders/cancel-all-after",
        {"timeout": timeout_seconds},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_place_order(
    symbol: SpotSymbol,
    order_type: SpotOrderType,
    amount: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Order amount in the base currency; pass a decimal string to preserve exact precision.",
        ),
    ],
    account_id: AccountId | None = None,
    price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Limit or stop-limit price; required for non-market orders.",
        ),
    ] = None,
    client_order_id: ClientOrderId | None = None,
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
    operator: StopOperator | None = None,
    self_match_prevent: Annotated[
        bool,
        Field(
            description="When true, ask HTX to prevent this order matching the same account's order."
        ),
    ] = False,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Place one HTX spot order, with optional client ID, stop trigger, and self-match prevention.

    Check spot_get_symbols first for precision and minimums. Market orders omit ``price``; non-market orders require it; stop-limit orders also require ``stop_price`` and ``operator``. The result means HTX accepted the request, not that it filled. The call is a dry run unless ``confirm=true`` and HTX_ENABLE_TRADING=true.
    """

    order_type = order_type.strip().lower()
    operator = _stop_operator(operator) if operator is not None else None
    symbol = _symbol(symbol)
    _validate_spot_order(order_type, amount, price, stop_price, operator)
    if client_order_id is not None:
        _text(client_order_id, "client_order_id")
    resolved = await _resolve_spot_account_id(account_id)
    body = _q(
        **{
            "account-id": resolved,
            "symbol": symbol,
            "type": order_type,
            "amount": decimal_to_text(amount),
            "price": _decimal_or_none(price),
            "source": source,
            "client-order-id": client_order_id,
            "self-match-prevent": 1 if self_match_prevent else 0,
            "stop-price": _decimal_or_none(stop_price),
            "operator": operator,
        }
    )
    return await _mutation("spot_place_order", "/v1/order/orders/place", body, confirm)


@mcp.tool(annotations=WRITE)
async def spot_cancel_order(
    order_id: ExchangeOrderId, confirm: Confirm = False
) -> dict[str, Any]:
    """Request cancellation of one HTX spot order by exchange order ID.

    Cancellation is not proof that no fill occurred; query the order or its match results afterward. The call is a dry run unless both confirmation gates pass.
    """

    order_id = _text(order_id, "order_id")
    return await _mutation(
        "spot_cancel_order",
        f"/v1/order/orders/{order_id}/submitcancel",
        {},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_cancel_by_client_id(
    client_order_id: ClientOrderId, confirm: Confirm = False
) -> dict[str, Any]:
    """Request cancellation of one HTX spot order by client order ID.

    Use the same client ID used during placement and query the resulting order afterward. The call is a dry run unless both confirmation gates pass.
    """

    client_order_id = _text(client_order_id, "client_order_id")
    return await _mutation(
        "spot_cancel_by_client_id",
        "/v1/order/orders/submitcancelclientorder",
        {"client-order-id": client_order_id},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_cancel_orders_by_ids(
    order_ids: Annotated[
        list[ExchangeOrderId],
        Field(
            min_length=1,
            max_length=50,
            description="1-50 HTX exchange order IDs to cancel.",
        ),
    ],
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation for 1-50 HTX spot orders by exchange order ID.

    This affects every supplied ID and is therefore destructive; use confirm=true only after reviewing the exact list. Query orders afterward to verify final states.
    """

    if not 1 <= len(order_ids) <= 50:
        raise ToolError("order_ids must contain between 1 and 50 IDs")
    normalized_ids = [_text(order_id, "order_ids item") for order_id in order_ids]
    return await _mutation(
        "spot_cancel_orders_by_ids",
        "/v1/order/orders/batchcancel",
        {"order-ids": normalized_ids},
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def spot_cancel_open_orders(
    account_id: AccountId | None = None,
    symbol: SpotSymbol | None = None,
    order_types: Annotated[
        list[SpotOrderType] | None,
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
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of matching open HTX spot orders.

    Filters can include account, symbol, order types, and side; omitting filters can affect many orders. The call is a dry run unless both confirmation gates pass.
    """

    if not 1 <= size <= 100:
        raise ToolError("size must be between 1 and 100")
    resolved = await _resolve_spot_account_id(account_id)
    normalized_symbols = None
    if symbol:
        normalized_symbols = ",".join(_symbol(item) for item in symbol.split(","))
    normalized_order_types = None
    if order_types:
        normalized_order_types = ",".join(
            _text(item, "order_types item") for item in order_types
        )
    body = _q(
        **{
            "account-id": resolved,
            "symbol": normalized_symbols,
            "types": normalized_order_types,
            "side": side,
            "size": size,
        }
    )
    return await _mutation(
        "spot_cancel_open_orders",
        "/v1/order/orders/batchCancelOpenOrders",
        body,
        confirm,
    )


# ---------------------------------------------------------------------------
# USDT-margined swap market data
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def futures_get_contracts(
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read HTX USDT-margined contract metadata and trading rules.

    Omit ``contract_code`` to list all products. Use the response to check contract status, volume precision, minimum volume, price limits, and supported leverage before trading.
    """

    return await _public(
        "/linear-swap-api/v1/swap_contract_info",
        contract_code=_contract(contract_code) if contract_code is not None else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_ticker(contract_code: ContractCode) -> dict[str, Any]:
    """Read the latest merged ticker for one HTX USDT-margined contract.

    Returns current price, best bid/ask, 24-hour statistics, and exchange timestamp for the supplied contract.
    """

    return await _public(
        "/linear-swap-ex/market/detail/merged", contract_code=_contract(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_tickers(
    contract_code: ContractCode | None = None,
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

    return await _public(
        "/linear-swap-ex/market/detail/batch_merged",
        contract_code=_contract(contract_code) if contract_code is not None else None,
        business_type=business_type,
    )


@mcp.tool(annotations=READ)
async def futures_get_depth(
    contract_code: ContractCode,
    depth_type: FuturesDepthType = "step0",
) -> dict[str, Any]:
    """Read the current order-book depth for one HTX USDT-margined contract.

    ``depth_type`` selects HTX's aggregation level from step0 through step19. This is public market data.
    """

    allowed = {f"step{i}" for i in range(0, 20)}
    if depth_type not in allowed:
        raise ToolError("depth_type must be a supported HTX step0..step19 value")
    return await _public(
        "/linear-swap-ex/market/depth",
        contract_code=_contract(contract_code),
        type=depth_type,
    )


@mcp.tool(annotations=READ)
async def futures_get_klines(
    contract_code: ContractCode,
    period: CandlePeriod = "1day",
    size: Annotated[
        int,
        Field(
            ge=1, le=2000, description="Number of candles to return; HTX allows 1-2000."
        ),
    ] = 100,
    from_time: MillisecondTimestamp | None = None,
    to_time: MillisecondTimestamp | None = None,
) -> dict[str, Any]:
    """Read historical OHLCV candlesticks for one HTX USDT-margined contract.

    Time bounds use Unix milliseconds; ``size`` is 1-2000. Use futures_get_contracts first if the contract's trading state or precision is unknown.
    """

    if not 1 <= size <= 2000:
        raise ToolError("size must be between 1 and 2000")
    _validate_time_range(from_time, to_time, max_age_ms=None)
    return await _public(
        "/linear-swap-ex/market/history/kline",
        contract_code=_contract(contract_code),
        period=period,
        size=size,
        **{"from": from_time, "to": to_time},
    )


@mcp.tool(annotations=READ)
async def futures_get_recent_trades(
    contract_code: ContractCode,
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
    return await _public(
        "/linear-swap-ex/market/history/trade",
        contract_code=_contract(contract_code),
        size=size,
    )


@mcp.tool(annotations=READ)
async def futures_get_index(contract_code: ContractCode) -> dict[str, Any]:
    """Read the index price used by one HTX USDT-margined contract.

    Use this alongside the mark/last price when assessing liquidation or trigger risk.
    """

    return await _public(
        "/linear-swap-api/v1/swap_index", contract_code=_contract(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_price_limit(contract_code: ContractCode) -> dict[str, Any]:
    """Read the current upper and lower price limits for one HTX USDT-margined contract.

    Check these limits before submitting a limit, trigger, take-profit, or stop-loss price.
    """

    return await _public(
        "/linear-swap-api/v1/swap_price_limit", contract_code=_contract(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_open_interest(
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read current open interest for one or all HTX USDT-margined contracts.

    Omit ``contract_code`` for the public batch result.
    """

    return await _public(
        "/linear-swap-api/v1/swap_open_interest",
        contract_code=_contract(contract_code) if contract_code is not None else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_funding_rate(contract_code: ContractCode) -> dict[str, Any]:
    """Read the current funding rate and next funding time for one HTX perpetual swap.

    This is market data; it does not predict funding or alter a position.
    """

    return await _public(
        "/linear-swap-api/v1/swap_funding_rate", contract_code=_contract(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_batch_funding_rate(
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read current funding rates for one or all HTX USDT-margined perpetual swaps.

    Omit ``contract_code`` for a batch response across supported contracts.
    """

    return await _public(
        "/linear-swap-api/v1/swap_batch_funding_rate",
        contract_code=_contract(contract_code) if contract_code is not None else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_historical_funding_rate(
    contract_code: ContractCode,
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

    _page(page_index, page_size)
    return await _public(
        "/linear-swap-api/v1/swap_historical_funding_rate",
        contract_code=_contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@mcp.tool(annotations=READ)
async def futures_get_api_state() -> dict[str, Any]:
    """Read the public HTX USDT-margined swap service state.

    Use this to distinguish exchange maintenance from a local connectivity or authentication failure.
    """

    return await _public("/linear-swap-api/v1/swap_api_state")


@mcp.tool(annotations=READ)
async def futures_get_risk_info(
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read HTX swap risk information, including insurance-fund and estimated clawback data.

    Omit ``contract_code`` for the exchange-wide result, or provide one contract for a focused query.
    """

    return await _public(
        "/linear-swap-api/v1/swap_risk_info",
        contract_code=_contract(contract_code) if contract_code is not None else None,
    )


@mcp.tool(annotations=READ)
async def futures_get_insurance_fund(
    contract_code: ContractCode,
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

    _page(page_index, page_size)
    return await _public(
        "/linear-swap-api/v1/swap_insurance_fund",
        contract_code=_contract(contract_code),
        page_index=page_index,
        page_size=page_size,
    )


@mcp.tool(annotations=READ)
async def futures_get_liquidation_orders(
    contract_code: ContractCode | None = None,
    trade_type: TradeTypeInput = "all",
    pair: ContractCode | None = None,
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    direct: PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
) -> dict[str, Any]:
    """Read public HTX liquidation-order records through the current v3 endpoint.

    Provide at least one of ``contract_code`` or ``pair`` as required by HTX; ``trade_type`` accepts readable values such as liquidate_long or liquidate_short. The time window is at most two hours and can be shifted within the supported history period.
    """

    trade_type = _trade_type(trade_type)
    if contract_code is None and pair is None:
        raise ToolError("contract_code or pair is required")
    _validate_time_range(start_time, end_time, max_window_ms=2 * 60 * 60 * 1000)
    if from_id is not None:
        _positive_integer(from_id, "from_id")
    body = _q(
        contract=_contract(contract_code) if contract_code is not None else None,
        trade_type=trade_type,
        pair=_contract(pair) if pair is not None else None,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
    )
    return await _public_post("/linear-swap-api/v3/swap_liquidation_orders", body)


@mcp.tool(annotations=READ)
async def futures_get_elite_ratios(
    contract_code: ContractCode,
    period: Annotated[
        Literal["5min", "15min", "30min", "60min", "4hour", "1day"],
        Field(description="Sentiment-ratio interval; 60min is the default."),
    ] = "60min",
) -> dict[str, Any]:
    """Read HTX top-trader account and position sentiment ratios for one contract.

    The response contains two raw HTX results, one for account ratio and one for position ratio, at the requested interval.
    """

    code = _contract(contract_code)
    return {
        "account_ratio": await _public(
            "/linear-swap-api/v1/swap_elite_account_ratio",
            contract_code=code,
            period=period,
        ),
        "position_ratio": await _public(
            "/linear-swap-api/v1/swap_elite_position_ratio",
            contract_code=code,
            period=period,
        ),
    }


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
# USDT-margined swap account and order inspection
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ)
async def futures_get_account_info(
    margin_mode: MarginMode = "isolated",
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined account equity and margin information.

    Choose isolated or cross margin and optionally narrow the result to one contract. Requires API read permission.
    """

    return await _private_post(
        _swap_endpoint("account_info", margin_mode), _swap_body(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_positions(
    margin_mode: MarginMode = "isolated",
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined positions.

    Use this before closing or reducing exposure; an empty data list means no position matched the optional contract filter.
    """

    return await _private_post(
        _swap_endpoint("position_info", margin_mode), _swap_body(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_account_position_info(
    margin_mode: MarginMode = "isolated",
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read combined authenticated HTX swap assets and positions.

    This is a convenient pre-trade snapshot for account equity, margin, and open positions in the selected margin mode.
    """

    stem = "account_position_info"
    return await _private_post(
        _swap_endpoint(stem, margin_mode), _swap_body(contract_code)
    )


@mcp.tool(annotations=READ)
async def futures_get_available_leverage(
    margin_mode: MarginMode = "isolated",
    contract_code: ContractCode | None = None,
) -> dict[str, Any]:
    """Read leverage values currently available to the authenticated HTX swap account.

    Provide ``contract_code`` when selecting leverage for one contract; use the result before futures_switch_leverage.
    """

    return await _private_post(
        _swap_endpoint("available_level_rate", margin_mode),
        _swap_body(contract_code),
    )


@mcp.tool(annotations=READ)
async def futures_get_open_orders(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
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
    trade_type: OrderTradeTypeInput = "all",
) -> dict[str, Any]:
    """Read current unfilled HTX USDT-margined orders.

    Use the readable ``trade_type`` filters and page fields to inspect orders without changing them. This does not include fills that are already closed.
    """

    _page(page_index, page_size)
    trade_type = _trade_type(trade_type, order_filter=True)
    body = _swap_body(
        contract_code,
        page_index=page_index,
        page_size=page_size,
        sort_by=sort_by,
        trade_type=trade_type,
    )
    return await _private_post(_swap_endpoint("openorders", margin_mode), body)


@mcp.tool(annotations=READ)
async def futures_get_order_info(
    contract_code: ContractCode,
    order_id: ExchangeOrderId,
    margin_mode: MarginMode = "isolated",
) -> dict[str, Any]:
    """Read current status information for one HTX USDT-margined order.

    Use this after placing or canceling an order to reconcile the exchange state; acceptance is not the same as execution.
    """

    order_id = _text(order_id, "order_id")
    return await _private_post(
        _swap_endpoint("order_info", margin_mode),
        _swap_body(contract_code, order_id=order_id),
    )


@mcp.tool(annotations=READ)
async def futures_get_order_detail(
    contract_code: ContractCode,
    order_id: ExchangeOrderId,
    margin_mode: MarginMode = "isolated",
    created_at: MillisecondTimestamp | None = None,
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

    _page(page_index, page_size)
    order_id = _text(order_id, "order_id")
    if created_at is not None:
        _positive_integer(created_at, "created_at")
    if order_type is not None and order_type not in {1, 2, 3, 4}:
        raise ToolError("order_type must be one of 1, 2, 3, or 4")
    return await _private_post(
        _swap_endpoint("order_detail", margin_mode),
        _swap_body(
            contract_code,
            order_id=order_id,
            created_at=created_at,
            order_type=order_type,
            page_index=page_index,
            page_size=page_size,
        ),
    )


@mcp.tool(annotations=READ)
async def futures_get_history_orders(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
    trade_type: TradeTypeInput = "all",
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
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    direct: PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
) -> dict[str, Any]:
    """Read historical HTX USDT-margined orders through the current v3 endpoint.

    Use readable trade filters, comma-separated status codes, and a maximum 48-hour time window; paginate with ``from_id`` from the response.
    """

    trade_type = _trade_type(trade_type)
    status = _status_list(status, allowed=FUTURES_ORDER_STATUSES)
    _validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        _positive_integer(from_id, "from_id")
    body = _q(
        contract=_contract(contract_code),
        trade_type=trade_type,
        type=type,
        status=status,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
    )
    return await _private_post(
        _swap_endpoint("hisorders", margin_mode, version="v3"), body
    )


@mcp.tool(annotations=READ)
async def futures_get_match_results(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
    trade_type: TradeTypeInput = "all",
    pair: ContractCode | None = None,
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    direct: PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
) -> dict[str, Any]:
    """Read historical HTX USDT-margined fills through the current v3 endpoint.

    Use ``pair`` only for cross-margin queries, bound the request to at most 48 hours, and paginate with the returned query ID.
    """

    trade_type = _trade_type(trade_type)
    if pair is not None and margin_mode != "cross":
        raise ToolError("pair is only supported for cross-margin match-result queries")
    _validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        _positive_integer(from_id, "from_id")
    body = _q(
        contract=_contract(contract_code),
        trade_type=trade_type,
        pair=_contract(pair) if pair is not None else None,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
    )
    return await _private_post(
        _swap_endpoint("matchresults", margin_mode, version="v3"), body
    )


@mcp.tool(annotations=READ)
async def futures_get_financial_records(
    margin_mode: MarginMode = "isolated",
    contract_code: ContractCode | None = None,
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
    start_time: MillisecondTimestamp | None = None,
    end_time: MillisecondTimestamp | None = None,
    direct: PageDirection = "prev",
    from_id: Annotated[
        int | None,
        Field(
            gt=0, description="Pagination cursor query_id from the previous response."
        ),
    ] = None,
) -> dict[str, Any]:
    """Read authenticated HTX USDT-margined financial records through the current v3 endpoint.

    For isolated mode provide ``contract_code`` or ``margin_account``; for cross mode the financial account is ``USDT``. Transaction types are comma-separated HTX codes and the time window is at most 48 hours.
    """

    if transaction_types is not None:
        transaction_types = _status_list(
            transaction_types, allowed=FINANCIAL_RECORD_TYPES
        )
    _validate_time_range(start_time, end_time, max_window_ms=48 * 60 * 60 * 1000)
    if from_id is not None:
        _positive_integer(from_id, "from_id")
    contract = _contract(contract_code) if contract_code is not None else None
    body = _q(
        contract=contract,
        mar_acct=_v3_margin_account(margin_mode, contract, margin_account),
        type=transaction_types,
        start_time=start_time,
        end_time=end_time,
        direct=direct,
        from_id=from_id,
    )
    return await _private_post("/linear-swap-api/v3/swap_financial_record", body)


# ---------------------------------------------------------------------------
# USDT-margined swap trading and strategy orders
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
async def futures_place_order(
    contract_code: ContractCode,
    volume: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Order volume in contract units; must be positive and respect contract precision/minimum.",
        ),
    ],
    direction: FuturesDirection,
    order_price_type: FuturesOrderPriceType = "limit",
    price: Annotated[
        Decimal | None,
        Field(
            gt=0,
            description="Limit/order price; required for limit, post_only, IOC, and FOK types.",
        ),
    ] = None,
    offset: FuturesOffset | None = None,
    lever_rate: Annotated[
        int | None,
        Field(
            gt=0,
            description="Leverage multiplier. Omit to use the account's current leverage.",
        ),
    ] = None,
    margin_mode: MarginMode = "isolated",
    reduce_only: Annotated[
        bool,
        Field(
            description="When true, the order may only reduce an existing position; it cannot increase exposure."
        ),
    ] = False,
    client_order_id: ClientOrderId | None = None,
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
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Place one HTX USDT-margined order with optional exchange-side take-profit and stop-loss protection.

    Inspect contract rules, account state, position mode, leverage, and price limits first. In hedge mode ``offset`` is required; in one-way mode it is normally ``both``. The result means HTX accepted the order instruction, not that a position was opened or closed. This call is a dry run unless ``confirm=true`` and HTX_ENABLE_TRADING=true.
    """

    _validate_futures_order(
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
        _text(client_order_id, "client_order_id")
    if tp_order_price is not None:
        _positive_number(tp_order_price, "tp_order_price")
    if sl_order_price is not None:
        _positive_number(sl_order_price, "sl_order_price")
    if tp_trigger_price is None and tp_order_price is not None:
        raise ToolError("tp_order_price requires tp_trigger_price")
    if sl_trigger_price is None and sl_order_price is not None:
        raise ToolError("sl_order_price requires sl_trigger_price")
    body = _swap_body(
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
    return await _mutation(
        "futures_place_order",
        _swap_endpoint("order", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_place_batch_orders(
    orders: Annotated[
        list[FuturesBatchOrder],
        Field(
            min_length=1,
            max_length=10,
            description="1-10 order objects; each item uses the same fields as futures_place_order.",
        ),
    ],
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
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
            volume = _positive_number(order["volume"], f"orders[{index}].volume")
            price = (
                _positive_number(order["price"], f"orders[{index}].price")
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
                _positive_number(
                    order["tp_trigger_price"], f"orders[{index}].tp_trigger_price"
                )
                if order.get("tp_trigger_price") is not None
                else None
            )
            sl_trigger_price = (
                _positive_number(
                    order["sl_trigger_price"], f"orders[{index}].sl_trigger_price"
                )
                if order.get("sl_trigger_price") is not None
                else None
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ToolError(
                f"orders[{index}] contains a non-numeric order field"
            ) from exc
        _validate_futures_order(
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
                _positive_number(
                    order["tp_order_price"], f"orders[{index}].tp_order_price"
                )
                if order.get("tp_order_price") is not None
                else None
            )
            sl_order_price = (
                _positive_number(
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
                "contract_code": _contract(order["contract_code"]),
                "volume": volume,
                "reduce_only": int(bool(order.get("reduce_only", False))),
            }
        )
    body = {"orders_data": normalized_orders}
    return await _mutation(
        "futures_place_batch_orders",
        _swap_endpoint("batchorder", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_order(
    contract_code: ContractCode,
    order_id: ExchangeOrderId | None = None,
    client_order_id: ClientOrderId | None = None,
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of one HTX USDT-margined order.

    Provide ``order_id`` or ``client_order_id``; if both are supplied HTX receives both identifiers. Query the order afterward to confirm the final status. The call is a dry run unless both confirmation gates pass.
    """

    if order_id is None and client_order_id is None:
        raise ToolError("order_id or client_order_id is required")
    if order_id is not None:
        order_id = _text(order_id, "order_id")
    if client_order_id is not None:
        client_order_id = _text(client_order_id, "client_order_id")
    body = _swap_body(
        contract_code,
        order_id=order_id,
        client_order_id=client_order_id,
    )
    return await _mutation(
        "futures_cancel_order", _swap_endpoint("cancel", margin_mode), body, confirm
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_all_orders(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of all open HTX USDT-margined orders for one contract.

    This is a broad destructive action for the selected margin mode and contract. Review the dry-run body before setting confirm=true, then query open orders afterward.
    """

    return await _mutation(
        "futures_cancel_all_orders",
        _swap_endpoint("cancelall", margin_mode),
        _swap_body(contract_code),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_switch_leverage(
    contract_code: ContractCode,
    lever_rate: Annotated[
        int,
        Field(
            gt=0,
            description="New leverage multiplier; HTX must support this value for the contract/account.",
        ),
    ],
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Change the leverage for one HTX USDT-margined contract.

    HTX may reject the change while orders or positions exist; first query available leverage and current account state. The call is a dry run unless both confirmation gates pass.
    """

    _positive_integer(lever_rate, "lever_rate")
    return await _mutation(
        "futures_switch_leverage",
        _swap_endpoint("switch_lever_rate", margin_mode),
        _swap_body(contract_code, lever_rate=lever_rate),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_lightning_close_position(
    contract_code: ContractCode,
    volume: Annotated[
        Decimal,
        Field(
            gt=0,
            description="Position volume to close in contract units; must be positive.",
        ),
    ],
    direction: FuturesDirection,
    margin_mode: MarginMode = "isolated",
    order_price_type: Annotated[
        Literal["market", "lightning_fok", "lightning_ioc"],
        Field(
            description="Close execution mode: market, lightning FOK, or lightning IOC."
        ),
    ] = "market",
    client_order_id: Annotated[
        int | None,
        Field(gt=0, description="Optional numeric client order ID for tracking."),
    ] = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request HTX's lightning close-position order for one USDT-margined position.

    ``direction`` is the order side that closes the position (buy closes short, sell closes long). This is an aggressive execution path; query positions first and verify the result afterward. The call is a dry run unless both confirmation gates pass.
    """

    _positive_number(volume, "volume")
    if client_order_id is not None:
        _positive_integer(client_order_id, "client_order_id")
    if margin_mode == "isolated":
        path = "/linear-swap-api/v1/swap_lightning_close_position"
    else:
        path = "/linear-swap-api/v1/swap_cross_lightning_close_position"
    body = _swap_body(
        contract_code,
        volume=volume,
        direction=direction,
        order_price_type=order_price_type,
        client_order_id=client_order_id,
    )
    return await _mutation("futures_lightning_close_position", path, body, confirm)


@mcp.tool(annotations=WRITE)
async def futures_place_trigger_order(
    contract_code: ContractCode,
    trigger_type: TriggerCondition,
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
    direction: FuturesDirection,
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
    offset: FuturesOffset | None = None,
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
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Place one HTX USDT-margined trigger order.

    ``trigger_type`` accepts readable greater_or_equal or less_or_equal values. A limit trigger requires ``order_price``; optimal_N uses the selected BBO depth. In hedge mode ``offset`` is required. The call is a dry run unless both confirmation gates pass.
    """

    trigger_type = _trigger_condition(trigger_type)
    _positive_number(trigger_price, "trigger_price")
    _positive_number(volume, "volume")
    if order_price_type == "limit" and order_price is None:
        raise ToolError("order_price is required for limit trigger orders")
    if order_price is not None:
        _positive_number(order_price, "order_price")
    if lever_rate is not None:
        _positive_integer(lever_rate, "lever_rate")
    body = _swap_body(
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
    return await _mutation(
        "futures_place_trigger_order",
        _swap_endpoint("trigger_order", margin_mode),
        body,
        confirm,
    )


@mcp.tool(annotations=READ)
async def futures_get_trigger_open_orders(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
    page_index: Annotated[
        int, Field(ge=1, description="1-based result page number.")
    ] = 1,
    page_size: Annotated[
        int,
        Field(ge=1, le=50, description="Results per page; this server accepts 1-50."),
    ] = 20,
    trade_type: OrderTradeTypeInput = "all",
) -> dict[str, Any]:
    """Read open HTX USDT-margined trigger orders.

    Use the readable trade filters and pagination fields to inspect strategy orders without changing them.
    """

    _page(page_index, page_size)
    trade_type = _trade_type(trade_type, order_filter=True)
    return await _private_post(
        _swap_endpoint("trigger_openorders", margin_mode),
        _swap_body(
            contract_code,
            page_index=page_index,
            page_size=page_size,
            trade_type=trade_type,
        ),
    )


@mcp.tool(annotations=READ)
async def futures_get_trigger_history(
    contract_code: ContractCode,
    margin_mode: MarginMode = "isolated",
    trade_type: OrderTradeTypeInput = "all",
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

    _page(page_index, page_size)
    trade_type = _trade_type(trade_type, order_filter=True)
    status = _status_list(status, allowed=TRIGGER_ORDER_STATUSES)
    _create_date(create_date)
    return await _private_post(
        _swap_endpoint("trigger_hisorders", margin_mode),
        _swap_body(
            contract_code,
            trade_type=trade_type,
            status=status,
            create_date=create_date,
            page_index=page_index,
            page_size=page_size,
            sort_by=sort_by,
        ),
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_trigger_order(
    contract_code: ContractCode,
    order_id: ExchangeOrderId,
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of one HTX USDT-margined trigger order.

    Query trigger history afterward to confirm whether the exchange canceled it. The call is a dry run unless both confirmation gates pass.
    """

    order_id = _text(order_id, "order_id")
    return await _mutation(
        "futures_cancel_trigger_order",
        _swap_endpoint("trigger_cancel", margin_mode),
        _swap_body(contract_code, order_id=order_id),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_cancel_all_trigger_orders(
    contract_code: ContractCode | None = None,
    direction: FuturesDirection | None = None,
    offset: Annotated[
        Literal["open", "close"] | None,
        Field(description="Optional trigger filter: open or close orders."),
    ] = None,
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Request cancellation of all HTX USDT-margined trigger orders matching the supplied filters.

    Omitting filters can cancel every trigger order in the selected margin mode, so inspect the dry-run request carefully. The call is a dry run unless both confirmation gates pass.
    """

    return await _mutation(
        "futures_cancel_all_trigger_orders",
        _swap_endpoint("trigger_cancelall", margin_mode),
        _swap_body(contract_code, direction=direction, offset=offset),
        confirm,
    )


@mcp.tool(annotations=WRITE)
async def futures_switch_position_mode(
    contract_code: ContractCode,
    position_mode: PositionMode,
    margin_mode: MarginMode = "isolated",
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Change one HTX USDT-margined contract between one-way and hedged position mode.

    Prefer ``one_way`` or ``hedged``; the legacy HTX values ``single_side`` and ``dual_side`` are also accepted. HTX may reject a mode change while orders or positions exist. The call is a dry run unless both confirmation gates pass.
    """

    return await _mutation(
        "futures_switch_position_mode",
        _swap_endpoint("switch_position_mode", margin_mode),
        _swap_body(contract_code, position_mode=_position_mode(position_mode)),
        confirm,
    )


@mcp.tool(annotations=READ)
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

    return await _private_post(
        "/linear-swap-api/v1/swap_balance_valuation",
        {"valuation_asset": valuation_asset},
    )


@mcp.tool(annotations=READ)
async def futures_get_api_trading_status() -> dict[str, Any]:
    """Read the authenticated HTX swap API-trading status indicator.

    Use this to diagnose whether the API key is permitted to trade before enabling a state-changing tool.
    """

    return await _private_get("/linear-swap-api/v1/swap_api_trading_status")


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

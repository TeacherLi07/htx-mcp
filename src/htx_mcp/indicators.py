"""Deterministic technical indicators calculated from HTX OHLCV candles."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from math import isqrt
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .precision import decimal_to_text


@dataclass(frozen=True)
class Candle:
    """A completed OHLCV candle in chronological order."""

    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


_PERIOD_MS = {
    "1min": 60_000,
    "5min": 300_000,
    "15min": 900_000,
    "30min": 1_800_000,
    "60min": 3_600_000,
    "4hour": 14_400_000,
    "1day": 86_400_000,
    "1week": 604_800_000,
    "1mon": 2_592_000_000,
}


def period_ms(period: str) -> int:
    try:
        return _PERIOD_MS[period]
    except KeyError as exc:
        raise ToolError(f"Unsupported candle period: {period}") from exc


def candles_from_htx(records: Any) -> list[Candle]:
    """Normalize HTX K-line records and return chronological valid candles."""

    if not isinstance(records, list):
        raise ToolError("HTX K-line response did not contain a candle list")
    candles: list[Candle] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        try:
            raw_time = int(record["id"])
            # HTX K-line IDs are normally seconds. Accept milliseconds defensively.
            open_time_ms = raw_time if raw_time >= 10_000_000_000 else raw_time * 1000
            candles.append(
                Candle(
                    open_time_ms=open_time_ms,
                    open=Decimal(str(record["open"])),
                    high=Decimal(str(record["high"])),
                    low=Decimal(str(record["low"])),
                    close=Decimal(str(record["close"])),
                    volume=Decimal(str(record.get("vol", record.get("amount")))),
                )
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ToolError(f"Invalid HTX candle at index {index}: {exc}") from exc
    return sorted(candles, key=lambda candle: candle.open_time_ms)


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _ema(values: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    previous = _mean(values[:period])
    result[period - 1] = previous
    alpha = Decimal(2) / Decimal(period + 1)
    for index in range(period, len(values)):
        previous = (values[index] - previous) * alpha + previous
        result[index] = previous
    return result


def _wilder(values: list[Decimal], period: int) -> list[Decimal | None]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    previous = _mean(values[:period])
    result[period - 1] = previous
    divisor = Decimal(period)
    for index in range(period, len(values)):
        previous = (previous * Decimal(period - 1) + values[index]) / divisor
        result[index] = previous
    return result


def _text(value: Decimal) -> str:
    text = decimal_to_text(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _positive_int(value: str, label: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ToolError(f"{label} must be a positive integer") from exc
    if result < 1:
        raise ToolError(f"{label} must be a positive integer")
    return result


def _parse_spec(spec: str) -> tuple[str, tuple[int | Decimal, ...], str]:
    if not isinstance(spec, str):
        raise ToolError("Each indicator must be a string")
    name, separator, raw_parameters = spec.lower().replace(" ", "").partition(":")
    aliases = {"ma": "sma", "boll": "bbands", "vol_sma": "volume_sma"}
    name = aliases.get(name, name)
    if not separator:
        raise ToolError(f"Indicator '{spec}' must include parameters after ':'")
    parameters = raw_parameters.split(",")
    expected = {
        "sma": 1,
        "ema": 1,
        "rsi": 1,
        "atr": 1,
        "volume_sma": 1,
        "bbands": 2,
        "macd": 3,
        "kdj": 3,
    }
    if name not in expected:
        supported = ", ".join(sorted(expected))
        raise ToolError(f"Unsupported indicator '{name}'. Supported: {supported}")
    if len(parameters) != expected[name] or any(not item for item in parameters):
        raise ToolError(f"Indicator '{spec}' has invalid parameters")
    if name == "bbands":
        period = _positive_int(parameters[0], "bbands period")
        try:
            multiplier = Decimal(parameters[1])
        except InvalidOperation as exc:
            raise ToolError("bbands multiplier must be a positive decimal") from exc
        if multiplier <= 0:
            raise ToolError("bbands multiplier must be a positive decimal")
        return name, (period, multiplier), f"bbands:{period},{_text(multiplier)}"
    values = tuple(_positive_int(value, f"{name} parameter") for value in parameters)
    if name == "macd" and values[0] >= values[1]:
        raise ToolError("macd fast period must be smaller than slow period")
    return name, values, f"{name}:{','.join(str(value) for value in values)}"


def calculate_indicators(
    candles: list[Candle], specs: list[str]
) -> dict[str, dict[str, Any]]:
    """Calculate requested indicators using Decimal-only arithmetic."""

    if not specs:
        raise ToolError("At least one indicator is required")
    parsed = [_parse_spec(spec) for spec in specs]
    if len({key for _, _, key in parsed}) != len(parsed):
        raise ToolError("Duplicate indicators are not allowed")
    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    volumes = [candle.volume for candle in candles]
    output: dict[str, dict[str, Any]] = {}

    for name, parameters, key in parsed:
        period = int(parameters[0])
        required = period
        if name == "macd":
            fast, slow, signal = (int(value) for value in parameters)
            required = slow + signal - 1
        elif name == "rsi" or name == "atr":
            required = period + 1
        elif name == "kdj":
            required = period
        if len(candles) < required:
            output[key] = {
                "status": "insufficient_data",
                "required_candles": required,
                "available_candles": len(candles),
            }
            continue

        with localcontext() as context:
            context.prec = 50
            if name == "sma":
                output[key] = {"value": _text(_mean(closes[-period:]))}
            elif name == "ema":
                value = _ema(closes, period)[-1]
                assert value is not None
                output[key] = {"value": _text(value)}
            elif name == "volume_sma":
                output[key] = {"value": _text(_mean(volumes[-period:]))}
            elif name == "bbands":
                multiplier = Decimal(parameters[1])
                middle = _mean(closes[-period:])
                variance = _mean([(value - middle) ** 2 for value in closes[-period:]])
                # Decimal has sqrt, but isqrt keeps compatibility with Python 3.10.
                scale = Decimal(10) ** 24
                deviation = Decimal(isqrt(int(variance * scale * scale))) / scale
                upper = middle + multiplier * deviation
                lower = middle - multiplier * deviation
                output[key] = {
                    "upper": _text(upper),
                    "middle": _text(middle),
                    "lower": _text(lower),
                }
            elif name == "rsi":
                changes = [
                    closes[index] - closes[index - 1] for index in range(1, len(closes))
                ]
                gains = [max(change, Decimal(0)) for change in changes]
                losses = [max(-change, Decimal(0)) for change in changes]
                average_gain = _wilder(gains, period)[-1]
                average_loss = _wilder(losses, period)[-1]
                assert average_gain is not None and average_loss is not None
                if average_loss == 0:
                    value = Decimal(100) if average_gain > 0 else Decimal(50)
                else:
                    relative_strength = average_gain / average_loss
                    value = Decimal(100) - Decimal(100) / (
                        Decimal(1) + relative_strength
                    )
                output[key] = {"value": _text(value)}
            elif name == "atr":
                ranges = [highs[0] - lows[0]]
                ranges.extend(
                    max(
                        highs[index] - lows[index],
                        abs(highs[index] - closes[index - 1]),
                        abs(lows[index] - closes[index - 1]),
                    )
                    for index in range(1, len(candles))
                )
                value = _wilder(ranges[1:], period)[-1]
                assert value is not None
                output[key] = {"value": _text(value)}
            elif name == "macd":
                fast, slow, signal = (int(value) for value in parameters)
                fast_values = _ema(closes, fast)
                slow_values = _ema(closes, slow)
                line = [
                    fast_value - slow_value
                    for fast_value, slow_value in zip(fast_values, slow_values)
                    if fast_value is not None and slow_value is not None
                ]
                signal_value = _ema(line, signal)[-1]
                assert signal_value is not None
                line_value = line[-1]
                output[key] = {
                    "macd": _text(line_value),
                    "signal": _text(signal_value),
                    "histogram": _text(line_value - signal_value),
                }
            elif name == "kdj":
                k_period, k_smoothing, d_smoothing = (
                    int(value) for value in parameters
                )
                raw_k: list[Decimal] = []
                for index in range(k_period - 1, len(candles)):
                    highest = max(highs[index - k_period + 1 : index + 1])
                    lowest = min(lows[index - k_period + 1 : index + 1])
                    raw_k.append(
                        Decimal(50)
                        if highest == lowest
                        else (closes[index] - lowest)
                        * Decimal(100)
                        / (highest - lowest)
                    )
                k_value = _ema(raw_k, k_smoothing)[-1]
                k_series = [
                    value for value in _ema(raw_k, k_smoothing) if value is not None
                ]
                d_value = _ema(k_series, d_smoothing)[-1]
                assert k_value is not None and d_value is not None
                output[key] = {
                    "k": _text(k_value),
                    "d": _text(d_value),
                    "j": _text(Decimal(3) * k_value - Decimal(2) * d_value),
                }
    return output

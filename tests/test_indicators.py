from decimal import Decimal

from htx_mcp.indicators import (
    Candle,
    calculate_indicator_values,
    calculate_indicators,
    format_indicator_value,
)


def test_format_indicator_value_limits_display_precision_by_indicator_type():
    result = {
        "ema": format_indicator_value(
            "ema:20",
            Decimal("2485.1205858261711495161150668639752709210675345087"),
            price_places=2,
        ),
        "rsi": format_indicator_value(
            "rsi:14", Decimal("45.34831469966155716373995995726382270507788020365")
        ),
        "macd": format_indicator_value(
            "macd:12,26,9",
            Decimal("-4.2803802918090137669631938291288606925422433177"),
            price_places=2,
        ),
        "kdj": format_indicator_value("kdj:9,3,3", Decimal("50.123456")),
    }

    assert result == {
        "ema": "2485.121",
        "rsi": "45.35",
        "macd": "-4.28038",
        "kdj": "50.12",
    }


def test_calculate_indicators_preserves_insufficient_data_metadata():
    result = calculate_indicators([], ["rsi:14"])

    assert result["rsi:14"]["status"] == "insufficient_data"
    assert result["rsi:14"]["required_candles"] == 15


def test_calculate_indicators_returns_compact_values():
    candles = [
        Candle(
            open_time_ms=index,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(1),
        )
        for index, close in enumerate(("1", "2", "2"))
    ]

    result = calculate_indicators(candles, ["sma:3"])

    assert result == {"sma:3": {"value": "1.6666667"}}


def test_display_rounding_does_not_change_indicator_calculation_values():
    candles = [
        Candle(
            open_time_ms=index,
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(1),
        )
        for index, close in enumerate(("1.23", "1.24", "1.27"))
    ]

    raw_value = calculate_indicator_values(candles, ["ema:2"])["ema:2"]["value"]
    displayed_value = calculate_indicators(candles, ["ema:2"])["ema:2"]["value"]

    assert raw_value == Decimal("1.2583333333333333333333333333333333333333333333333")
    assert displayed_value == "1.258"

from htx_mcp.indicators import compact_indicator_output


def test_compact_indicator_output_limits_display_precision_by_indicator_type():
    result = compact_indicator_output(
        {
            "ema:20": {"value": "2485.1203858261711495161150668639752709210675345087"},
            "rsi:14": {"value": "45.34831469966155716373995995726382270507788020365"},
            "macd:12,26,9": {
                "macd": "-4.2803802918090137669631938291288606925422433177",
                "signal": "-2.4715441168683138256739052761540792157528037878869",
                "histogram": "-1.8088361749406999412892885529747814767894395298131",
            },
            "kdj:9,3,3": {"k": "50.123456", "d": "49.987654", "j": "50"},
        }
    )

    assert result == {
        "ema:20": {"value": "2485.1204"},
        "rsi:14": {"value": "45.3483"},
        "macd:12,26,9": {
            "macd": "-4.2803803",
            "signal": "-2.4715441",
            "histogram": "-1.8088362",
        },
        "kdj:9,3,3": {"k": "50.1235", "d": "49.9877", "j": "50"},
    }


def test_compact_indicator_output_preserves_insufficient_data_metadata():
    result = compact_indicator_output(
        {
            "rsi:14": {
                "status": "insufficient_data",
                "required_candles": 15,
                "available_candles": 1,
            }
        }
    )

    assert result["rsi:14"]["status"] == "insufficient_data"
    assert result["rsi:14"]["required_candles"] == 15

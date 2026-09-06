"""Exact decimal helpers shared by HTX request and MCP model layers."""

from __future__ import annotations

from decimal import Decimal


def decimal_to_text(value: Decimal) -> str:
    """Serialize a finite Decimal as fixed-point text without float rounding."""

    if not value.is_finite():
        raise ValueError("Decimal values sent to HTX must be finite")
    return format(value, "f")

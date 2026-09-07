"""Safely switch an HTX USDT-swap unified account to the legacy account type.

This script deliberately uses credentials distinct from the MCP server's
HTX_API_KEY/HTX_API_SECRET pair. It never loads .env files.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from htx_mcp.client import HtxClient, HtxConfig, HtxError

ACCOUNT_TYPE_PATH = "/linear-swap-api/v3/swap_unified_account_type"
SWITCH_ACCOUNT_TYPE_PATH = "/linear-swap-api/v3/swap_switch_account_type"


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _account_type(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    value = data.get("account_type") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def switch_to_non_unified(
    client: HtxClient, *, futures_base_url: str, confirm: bool
) -> dict[str, Any]:
    """Read, optionally switch, and verify the HTX USDT-swap account type."""

    before = await client.request(
        "GET", ACCOUNT_TYPE_PATH, private=True, base_url=futures_base_url
    )
    account_type = _account_type(before)
    if account_type == 1:
        return {"status": "already_non_unified", "account_type": account_type}
    if account_type != 2:
        return {"status": "unexpected_account_type", "account_type": account_type}
    if not confirm:
        return {"status": "confirmation_required", "account_type": account_type}

    await client.request(
        "POST",
        SWITCH_ACCOUNT_TYPE_PATH,
        body={"account_type": 1},
        private=True,
        base_url=futures_base_url,
    )
    after = await client.request(
        "GET", ACCOUNT_TYPE_PATH, private=True, base_url=futures_base_url
    )
    verified_type = _account_type(after)
    return {
        "status": "switched" if verified_type == 1 else "verification_failed",
        "account_type": verified_type,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Switch an HTX USDT-swap unified account to account type 1."
    )
    parser.add_argument(
        "--confirm-switch-to-non-unified",
        action="store_true",
        help="Actually send the account-type change request.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    try:
        config = HtxConfig(
            api_key=_required_environment("HTX_SWITCH_API_KEY"),
            api_secret=_required_environment("HTX_SWITCH_API_SECRET"),
            futures_base_url=os.getenv(
                "HTX_SWITCH_FUTURES_API_BASE_URL", "https://api.hbdm.com"
            ).rstrip("/"),
            timeout_seconds=float(os.getenv("HTX_SWITCH_TIMEOUT_SECONDS", "20")),
        )
        client = HtxClient(config)
        try:
            result = await switch_to_non_unified(
                client,
                futures_base_url=config.futures_base_url,
                confirm=args.confirm_switch_to_non_unified,
            )
        finally:
            await client.close()
    except (HtxError, RuntimeError, ValueError) as error:
        print(f"Switch failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    status = result["status"]
    if status == "confirmation_required":
        print(
            "Account type is unified (2). Re-run with "
            "--confirm-switch-to-non-unified after all U-margined positions and "
            "open orders are cleared."
        )
        return 2
    if status == "switched":
        print("Account type changed and verified as non-unified (1).")
        return 0
    if status == "already_non_unified":
        print("Account is already non-unified (1); no change was made.")
        return 0
    print(
        "HTX returned an unexpected account type; no change was made.", file=sys.stderr
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

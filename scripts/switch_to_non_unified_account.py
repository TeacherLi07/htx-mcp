"""Safely switch an HTX USDT-swap unified account to the legacy account type.

This script deliberately uses credentials distinct from the MCP server's
HTX_API_KEY/HTX_API_SECRET pair. It never loads .env files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any

from htx_mcp.client import HtxApiError, HtxClient, HtxConfig, HtxError

ACCOUNT_TYPE_PATH = "/linear-swap-api/v3/swap_unified_account_type"
SWITCH_ACCOUNT_TYPE_PATH = "/linear-swap-api/v3/swap_switch_account_type"
SENSITIVE_KEYS = {"accesskeyid", "api_key", "api_secret", "secret", "signature"}
SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)(accesskeyid|api[_-]?key|api[_-]?secret|secret|signature)=([^&\s]+)"
)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _account_type(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    value = data.get("account_type") if isinstance(data, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return SENSITIVE_QUERY_VALUE.sub(r"\1=<redacted>", value)
    return value


class DiagnosticClient:
    """Record sanitized request/response pairs without retaining signed URLs."""

    def __init__(self, client: HtxClient):
        self.client = client
        self.events: list[dict[str, Any]] = []

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        base_url = str(kwargs["base_url"]).rstrip("/")
        event: dict[str, Any] = {
            "request": {
                "method": method,
                "url": f"{base_url}{path}",
                "authenticated_query_parameters": "<redacted>",
                "body": _redact(kwargs.get("body")),
            }
        }
        try:
            response = await self.client.request(method, path, **kwargs)
        except HtxApiError as error:
            event["response"] = _redact(
                error.payload if error.payload is not None else {"message": str(error)}
            )
            self.events.append(event)
            raise
        event["response"] = _redact(response)
        self.events.append(event)
        return response


def _print_diagnostics(events: list[dict[str, Any]]) -> None:
    print("HTX support diagnostic (authentication query parameters are redacted):")
    print(json.dumps(events, ensure_ascii=False, indent=2, default=str))


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
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print sanitized request and response JSON for an HTX support ticket.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    diagnostic_client: DiagnosticClient | None = None
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
        diagnostic_client = DiagnosticClient(client)
        try:
            result = await switch_to_non_unified(
                diagnostic_client,
                futures_base_url=config.futures_base_url,
                confirm=args.confirm_switch_to_non_unified,
            )
        finally:
            await client.close()
    except (HtxError, RuntimeError, ValueError) as error:
        print(f"Switch failed: {type(error).__name__}: {error}", file=sys.stderr)
        if diagnostic_client is not None:
            _print_diagnostics(diagnostic_client.events)
        return 1

    if args.diagnostics:
        _print_diagnostics(diagnostic_client.events)

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

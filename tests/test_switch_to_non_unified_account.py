import asyncio
import runpy
from pathlib import Path

SCRIPT = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "switch_to_non_unified_account.py")
)
switch_to_non_unified = SCRIPT["switch_to_non_unified"]


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return next(self.responses)


def test_switch_requires_explicit_confirmation_for_unified_account():
    client = FakeClient([{"code": 200, "data": {"account_type": 2}}])

    result = asyncio.run(
        switch_to_non_unified(
            client, futures_base_url="https://futures.test", confirm=False
        )
    )

    assert result == {"status": "confirmation_required", "account_type": 2}
    assert client.calls == [
        (
            "GET",
            "/linear-swap-api/v3/swap_unified_account_type",
            {"private": True, "base_url": "https://futures.test"},
        )
    ]


def test_switch_changes_and_verifies_unified_account_type():
    client = FakeClient(
        [
            {"code": 200, "data": {"account_type": 2}},
            {"code": 200, "data": {}},
            {"code": 200, "data": {"account_type": 1}},
        ]
    )

    result = asyncio.run(
        switch_to_non_unified(
            client, futures_base_url="https://futures.test", confirm=True
        )
    )

    assert result == {"status": "switched", "account_type": 1}
    assert client.calls[1] == (
        "POST",
        "/linear-swap-api/v3/swap_switch_account_type",
        {
            "body": {"account_type": 1},
            "private": True,
            "base_url": "https://futures.test",
        },
    )

import asyncio
import base64
import hashlib
import hmac
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from htx_mcp.client import (
    HtxApiError,
    HtxClient,
    HtxConfig,
    HtxConfigurationError,
    _canonical_query,
    _env_optional,
    ensure_confirmation,
    sign_request,
)


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse({"status": "ok", "data": {"accepted": True}})

    async def aclose(self):
        pass


class RetryingHttp:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    async def request(self, method, url, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.PoolTimeout("pool exhausted")
        return FakeResponse({"status": "ok", "data": {"accepted": True}})


def test_canonical_query_uses_ascii_order_and_percent20():
    assert _canonical_query({"z": "two words", "a": "!"}) == "a=%21&z=two%20words"


def test_signature_matches_independent_hmac_calculation():
    params = {
        "Timestamp": "2017-05-11T15:19:30",
        "order-id": "1234567890",
        "AccessKeyId": "access",
        "SignatureMethod": "HmacSHA256",
        "SignatureVersion": "2",
    }
    canonical = _canonical_query(params)
    message = f"GET\napi.huobi.pro\n/v1/order/orders\n{canonical}".encode()
    expected = base64.b64encode(
        hmac.new(b"secret", message, hashlib.sha256).digest()
    ).decode()
    assert (
        sign_request(
            method="GET",
            host="api.huobi.pro",
            path="/v1/order/orders",
            params=params,
            secret="secret",
        )
        == expected
    )


def test_private_get_signs_endpoint_query():
    fake = FakeHttp()
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret"),
        http=fake,
    )
    result = asyncio.run(
        client.request("GET", "/v1/test", query={"symbol": "btcusdt"}, private=True)
    )
    assert result["data"]["accepted"] is True
    method, url, kwargs = fake.calls[0]
    query = parse_qs(urlsplit(url).query)
    assert method == "GET"
    assert query["symbol"] == ["btcusdt"]
    assert query["AccessKeyId"] == ["key"]
    assert "Signature" in query
    assert kwargs == {}


def test_private_post_keeps_business_fields_in_json_body():
    fake = FakeHttp()
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret"),
        http=fake,
    )
    asyncio.run(
        client.request(
            "POST", "/v1/test", body={"contract_code": "BTC-USDT"}, private=True
        )
    )
    _, url, kwargs = fake.calls[0]
    query = parse_qs(urlsplit(url).query)
    assert "contract_code" not in query
    assert kwargs["json"] == {"contract_code": "BTC-USDT"}


def test_malformed_json_envelope_raises_a_sanitized_api_error():
    class MalformedHttp(FakeHttp):
        async def request(self, method, url, **kwargs):
            return FakeResponse(["not", "an", "HTX envelope"])

    client = HtxClient(HtxConfig(), http=MalformedHttp())

    with pytest.raises(HtxApiError, match="malformed JSON envelope"):
        asyncio.run(client.request("GET", "/v1/test"))


def test_http_error_does_not_echo_sensitive_response_data():
    class ErrorHttp(FakeHttp):
        async def request(self, method, url, **kwargs):
            response = FakeResponse(
                {"message": "https://example.test/?AccessKeyId=key&Signature=secret"}
            )
            response.status_code = 401
            return response

    client = HtxClient(HtxConfig(), http=ErrorHttp())

    with pytest.raises(HtxApiError) as error:
        asyncio.run(client.request("GET", "/v1/test"))

    assert "AccessKeyId" not in str(error.value)
    assert "Signature" not in str(error.value)


def test_decimal_business_fields_are_sent_as_exact_fixed_point_strings():
    fake = FakeHttp()
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret"),
        http=fake,
    )
    asyncio.run(
        client.request(
            "POST",
            "/v1/test",
            body={
                "volume": Decimal("0.00000001"),
                "price": Decimal("60000.123456789012345678"),
            },
            private=True,
        )
    )
    _, _, kwargs = fake.calls[0]
    assert kwargs["json"] == {
        "volume": "0.00000001",
        "price": "60000.123456789012345678",
    }


def test_mutation_preview_is_default_and_has_no_secret():
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret", enable_trading=False),
        http=FakeHttp(),
    )
    preview = ensure_confirmation(
        client,
        tool_name="spot_place_order",
        confirm=False,
        request={"body": {"symbol": "btcusdt"}},
    )
    assert preview["dry_run"] is True
    assert "secret" not in str(preview)


def test_confirmed_mutation_explains_how_to_enable_execution():
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret", enable_trading=False),
        http=FakeHttp(),
    )

    preview = ensure_confirmation(
        client,
        tool_name="spot_place_order",
        confirm=True,
        request={"path": "/v1/order/orders/place", "body": {"symbol": "btcusdt"}},
    )

    assert preview is not None
    assert preview["dry_run"] is True
    assert "HTX_ENABLE_TRADING=false" in preview["reason"]
    assert "HTX_ENABLE_TRADING=true" in preview["reason"]


def test_swap_mutation_can_be_disabled_while_spot_trading_remains_enabled():
    client = HtxClient(
        HtxConfig(
            api_key="key",
            api_secret="secret",
            enable_trading=True,
            enable_swap_trading=False,
        ),
        http=FakeHttp(),
    )

    swap_preview = ensure_confirmation(
        client,
        tool_name="futures_place_order",
        confirm=True,
        request={"path": "/linear-swap-api/v1/swap_order", "body": {}},
    )
    spot_permission = ensure_confirmation(
        client,
        tool_name="spot_place_order",
        confirm=True,
        request={"path": "/v1/order/orders/place", "body": {}},
    )

    assert swap_preview["dry_run"] is True
    assert "HTX_ENABLE_SWAP_TRADING=false" in swap_preview["reason"]
    assert spot_permission is None


def test_v5_swap_mutation_obeys_the_independent_swap_trading_gate():
    client = HtxClient(
        HtxConfig(
            api_key="key",
            api_secret="secret",
            enable_trading=True,
            enable_swap_trading=False,
        ),
        http=FakeHttp(),
    )

    preview = ensure_confirmation(
        client,
        tool_name="v5_submit_order",
        confirm=True,
        request={"path": "/v5/trade/order", "body": {}},
    )

    assert preview is not None
    assert preview["dry_run"] is True
    assert "HTX_ENABLE_SWAP_TRADING=false" in preview["reason"]
    assert "HTX_ENABLE_SWAP_TRADING=true" in preview["reason"]


def test_optional_environment_values_are_trimmed(monkeypatch):
    monkeypatch.setenv("HTX_TEST_VALUE", "  api-key-with-newline  ")
    assert _env_optional("HTX_TEST_VALUE") == "api-key-with-newline"
    monkeypatch.setenv("HTX_TEST_VALUE", "   ")
    assert _env_optional("HTX_TEST_VALUE") is None


def test_get_retries_transient_pool_timeout():
    http = RetryingHttp(failures=1)
    client = HtxClient(
        HtxConfig(read_retry_attempts=1),
        http=http,
    )

    result = asyncio.run(client.request("GET", "/market/detail/merged"))

    assert result["data"]["accepted"] is True
    assert http.calls == 2


def test_post_does_not_retry_transient_pool_timeout():
    http = RetryingHttp(failures=1)
    client = HtxClient(
        HtxConfig(read_retry_attempts=2),
        http=http,
    )

    with pytest.raises(HtxApiError, match="PoolTimeout"):
        asyncio.run(client.request("POST", "/v1/order/orders/place", body={}))

    assert http.calls == 1


def test_rejects_invalid_read_retry_tuning(monkeypatch):
    monkeypatch.setenv("HTX_READ_RETRY_ATTEMPTS", "-1")
    with pytest.raises(HtxConfigurationError, match="non-negative integer"):
        HtxConfig.from_env()

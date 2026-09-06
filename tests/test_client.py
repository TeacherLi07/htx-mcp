import asyncio
import base64
import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

from htx_mcp.client import HtxClient, HtxConfig, _canonical_query, ensure_confirmation, sign_request


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
    expected = base64.b64encode(hmac.new(b"secret", message, hashlib.sha256).digest()).decode()
    assert sign_request(
        method="GET",
        host="api.huobi.pro",
        path="/v1/order/orders",
        params=params,
        secret="secret",
    ) == expected


def test_private_get_signs_endpoint_query():
    fake = FakeHttp()
    client = HtxClient(
        HtxConfig(api_key="key", api_secret="secret"),
        http=fake,
    )
    result = asyncio.run(client.request("GET", "/v1/test", query={"symbol": "btcusdt"}, private=True))
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
    asyncio.run(client.request("POST", "/v1/test", body={"contract_code": "BTC-USDT"}, private=True))
    _, url, kwargs = fake.calls[0]
    query = parse_qs(urlsplit(url).query)
    assert "contract_code" not in query
    assert kwargs["json"] == {"contract_code": "BTC-USDT"}


def test_mutation_preview_is_default_and_has_no_secret():
    client = HtxClient(HtxConfig(api_key="key", api_secret="secret", enable_trading=False), http=FakeHttp())
    preview = ensure_confirmation(
        client,
        tool_name="spot_place_order",
        confirm=False,
        request={"body": {"symbol": "btcusdt"}},
    )
    assert preview["dry_run"] is True
    assert "secret" not in str(preview)

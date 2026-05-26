import httpx

from app.config import settings
from app.services.dhan_client import DHAN_BASE_URL, RealDhanClient


class FakeHTTPClient:
    def __init__(self, response, recorder):
        self.response = response
        self.recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers):
        self.recorder["method"] = "GET"
        self.recorder["url"] = url
        self.recorder["headers"] = headers
        return self.response

    def post(self, url, json, headers):
        self.recorder["method"] = "POST"
        self.recorder["url"] = url
        self.recorder["json"] = json
        self.recorder["headers"] = headers
        return self.response


def patch_http_client(monkeypatch, response, recorder):
    def fake_client(*args, **kwargs):
        recorder["timeout"] = kwargs.get("timeout")
        return FakeHTTPClient(response, recorder)

    monkeypatch.setattr("app.services.dhan_client.httpx.Client", fake_client)


def test_validate_token_uses_v2_profile_endpoint(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json={"dhanClientId": "1000000001", "tokenValidity": "30/03/2025 15:37"})
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().validate_token(client_id="1000000001", access_token="token")

    assert result.success is True
    assert recorder["method"] == "GET"
    assert recorder["url"] == f"{DHAN_BASE_URL}/profile"
    assert recorder["headers"]["client-id"] == "1000000001"
    assert recorder["headers"]["access-token"] == "token"
    assert recorder["headers"]["Accept"] == "application/json"


def test_validate_token_returns_dhan_error_message(monkeypatch):
    recorder = {}
    response = httpx.Response(401, json={"errorMessage": "Invalid token or token expired"})
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().validate_token(client_id="1000000001", access_token="token")

    assert result.success is False
    assert result.status_code == 401
    assert result.message == "Dhan token validation failed: Invalid token or token expired"


def test_validate_token_rejects_profile_for_different_client_id(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json={"dhanClientId": "9999999999", "tokenValidity": "30/03/2025 15:37"})
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().validate_token(client_id="1000000001", access_token="token")

    assert result.success is False
    assert result.message == (
        "Dhan token valid, but it belongs to client ID 9999999999, "
        "not configured client ID 1000000001."
    )


def test_place_order_uses_v2_orders_endpoint(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json={"orderId": "112111182198", "orderStatus": "PENDING"})
    patch_http_client(monkeypatch, response, recorder)
    monkeypatch.setattr(
        "app.services.dhan_client.get_outgoing_ip",
        lambda timeout=2.0: {"outgoing_ip": "203.0.113.10", "ok": True, "error": None},
    )
    monkeypatch.setattr("app.services.dhan_client._market_is_open", lambda: True)
    monkeypatch.setattr("app.services.dhan_client.log_order_event", lambda event: event)
    # Payload must include all Dhan v2 required fields including the three
    # added in the compliance audit: disclosedQuantity, triggerPrice, afterMarketOrder.
    payload = {
        "dhanClientId": "1000000001",
        "transactionType": "BUY",
        "exchangeSegment": "NSE_FNO",
        "productType": "INTRADAY",
        "orderType": "MARKET",
        "validity": "DAY",
        "securityId": "123456",
        "quantity": 1,
        "disclosedQuantity": 0,
        "price": 0,
        "triggerPrice": 0,
        "afterMarketOrder": False,
    }

    result = RealDhanClient().place_order(
        client_id="1000000001",
        access_token="token",
        payload=payload,
    )

    assert result.success is True
    assert result.order_id == "112111182198"
    assert recorder["method"] == "POST"
    assert recorder["url"] == f"{DHAN_BASE_URL}/orders"
    assert recorder["json"] == payload


def test_place_order_refuses_raw_pine_payload_before_http(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json={"orderId": "should-not-be-used"})
    patch_http_client(monkeypatch, response, recorder)
    monkeypatch.setattr(
        "app.services.dhan_client.get_outgoing_ip",
        lambda timeout=2.0: {"outgoing_ip": "203.0.113.10", "ok": True, "error": None},
    )
    monkeypatch.setattr("app.services.dhan_client._market_is_open", lambda: True)
    monkeypatch.setattr("app.services.dhan_client.log_order_event", lambda event: event)

    result = RealDhanClient().place_order(
        client_id="1000000001",
        access_token="token",
        payload={
            "alertType": "multi_leg_order",
            "order_legs": [{"transactionType": "B", "strike_price": "22500.0"}],
        },
    )

    assert result.success is False
    assert result.status == "PAYLOAD_INVALID"
    assert result.error == "BUG: Raw Pine payload reached Dhan client. This must be normalized first."
    assert "method" not in recorder


def test_get_fund_limit_uses_v2_fundlimit_endpoint(monkeypatch):
    recorder = {}
    response = httpx.Response(
        200,
        json={
            "dhanClientId": "1000000001",
            "availabelBalance": 98440.0,
            "withdrawableBalance": 98310.0,
            "utilizedAmount": 15202.0,
        },
    )
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().get_fund_limit(client_id="1000000001", access_token="token")

    assert result.success is True
    assert result.available_balance == 98440.0
    assert result.withdrawable_balance == 98310.0
    assert result.utilized_amount == 15202.0
    assert recorder["method"] == "GET"
    assert recorder["url"] == f"{DHAN_BASE_URL}/fundlimit"


# ===========================================================================
# FIX 2: Header building via _headers() — access-token and Content-Type always
# present; client-id conditional on DHAN_SEND_CLIENT_ID_HEADER setting.
# ===========================================================================

def test_get_order_book_uses_v2_orders_endpoint(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json=[{"orderId": "112111182198", "orderStatus": "PENDING"}])
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().get_order_book(client_id="1000000001", access_token="token")

    assert result.success is True
    assert result.items == [{"orderId": "112111182198", "orderStatus": "PENDING"}]
    assert recorder["method"] == "GET"
    assert recorder["url"] == f"{DHAN_BASE_URL}/orders"


def test_get_positions_snapshot_uses_v2_positions_endpoint(monkeypatch):
    recorder = {}
    response = httpx.Response(200, json=[{"tradingSymbol": "NIFTY 26 MAY 23900 CALL", "netQty": 65}])
    patch_http_client(monkeypatch, response, recorder)

    result = RealDhanClient().get_positions_snapshot(client_id="1000000001", access_token="token")

    assert result.success is True
    assert result.items == [{"tradingSymbol": "NIFTY 26 MAY 23900 CALL", "netQty": 65}]
    assert recorder["method"] == "GET"
    assert recorder["url"] == f"{DHAN_BASE_URL}/positions"


def test_headers_always_include_access_token_and_content_type(monkeypatch):
    """access-token and Content-Type must always be present in Dhan request headers."""
    monkeypatch.setattr(settings, "DHAN_SEND_CLIENT_ID_HEADER", True)
    headers = RealDhanClient()._headers("1000000001", "my-jwt-token")
    assert headers["access-token"] == "my-jwt-token"
    assert headers["Content-Type"] == "application/json"


def test_headers_include_client_id_when_config_true(monkeypatch):
    """When DHAN_SEND_CLIENT_ID_HEADER=True, client-id must be in headers."""
    monkeypatch.setattr(settings, "DHAN_SEND_CLIENT_ID_HEADER", True)
    headers = RealDhanClient()._headers("1000000001", "my-jwt-token")
    assert "client-id" in headers
    assert headers["client-id"] == "1000000001"


def test_headers_omit_client_id_when_config_false(monkeypatch):
    """When DHAN_SEND_CLIENT_ID_HEADER=False, client-id must NOT be in headers."""
    monkeypatch.setattr(settings, "DHAN_SEND_CLIENT_ID_HEADER", False)
    headers = RealDhanClient()._headers("1000000001", "my-jwt-token")
    assert "client-id" not in headers
    # access-token and Content-Type must still be present
    assert "access-token" in headers
    assert "Content-Type" in headers


def test_headers_access_token_is_never_masked_in_actual_request(monkeypatch):
    """The raw (unmasked) access token must be sent in the actual HTTP request, not a masked version."""
    monkeypatch.setattr(settings, "DHAN_SEND_CLIENT_ID_HEADER", True)
    raw_token = "Bearer-real-jwt-abc123"
    headers = RealDhanClient()._headers("1000000001", raw_token)
    # Must be the real token, not a masked version like ****abc123
    assert headers["access-token"] == raw_token
    assert "****" not in headers["access-token"]

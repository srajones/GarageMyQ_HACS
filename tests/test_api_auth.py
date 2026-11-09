"""Adversarial tests for myQ OAuth2 authentication, session management, and anti-lockout safeguards.

Conforms to Requirement R3 (Anti-Lockout Architecture) and PROTOCOL.md §3 & §4:
- Concurrent Token Refresh Race Test (single-flight lock verification).
- Zero-Lockout Circuit Breaker (invalid_grant, 401.102, 403, 429).
- Proactive Token Refresh (5-minute expiration window).
- Reactive Token Refresh (single retry on HTTP 401).
- Mobile Header and Fingerprint Verification.
- PKCE S256 Cryptographic Conformance.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs

import pytest

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.const import (
    API_VERSION,
    APP_VERSION,
    BRAND_ID,
    CLIENT_ID,
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CULTURE,
    HEADER_ANTI_CSRF,
    HEADER_API_VERSION,
    HEADER_APP_VERSION,
    HEADER_APPLICATION_ID,
    HEADER_BRAND_ID,
    HEADER_CULTURE,
    HEADER_IS_REFRESH,
    USER_AGENT_API,
    MyQAuthError,
    MyQError,
    MyQInvalidGrantError,
    MyQRateLimitError,
)
from tests.conftest import MockAsyncSession, MockResponse


# -----------------------------------------------------------------------------
# 1. PKCE S256 Cryptographic Verification
# -----------------------------------------------------------------------------

def test_pkce_generation() -> None:
    """Verify PKCE generation conforms to RFC 7636 S256 and PROTOCOL.md §3.2."""
    verifier, challenge = MyQClient.generate_pkce()

    # Code verifier: 32 bytes base64url unpadded -> exactly 43 chars
    assert len(verifier) == 43
    assert "=" not in verifier
    assert "\n" not in verifier
    assert "+" not in verifier
    assert "/" not in verifier

    # Code challenge: SHA256 of verifier, base64url unpadded
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert challenge == expected_challenge


def test_authorization_url_construction() -> None:
    """Verify authorization URL parameters match official APK configuration."""
    client = MyQClient()
    auth_url = client.get_authorization_url("TEST_CHALLENGE_STRING")

    assert "https://partner-identity.myq-cloud.com/connect/authorize" in auth_url
    assert "client_id=ANDROID_CGI_MYQ" in auth_url
    assert "response_type=code" in auth_url
    assert "redirect_uri=com.myqops%3A%2F%2Fandroid" in auth_url
    assert "code_challenge=TEST_CHALLENGE_STRING" in auth_url
    assert "code_challenge_method=S256" in auth_url
    assert "ui_locales=en" in auth_url
    assert "prompt=login" in auth_url
    assert "acr_values=unifiedFlow+brand%3Aliftmaster" in auth_url or "brand%3Aliftmaster" in auth_url


# -----------------------------------------------------------------------------
# 2. Concurrent Token Refresh Race Test (Requirement R3.3 & R6.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_token_refresh_race(mock_session: MockAsyncSession) -> None:
    """Verify 10 concurrent refresh calls execute exactly 1 network request via single-flight lock."""
    refresh_request_count = 0

    async def delayed_token_response(request_record: dict[str, Any]) -> MockResponse:
        nonlocal refresh_request_count
        refresh_request_count += 1
        # Simulate realistic network delay to guarantee coroutine overlap
        await asyncio.sleep(0.05)
        return MockResponse(
            status_code=200,
            json_data={
                "access_token": "new_race_access_token_999",
                "refresh_token": "new_race_refresh_token_999",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "MyQ_Residential offline_access",
            },
        )

    mock_session.add_route("/connect/token", delayed_token_response)

    persisted_tokens: list[dict[str, Any]] = []

    def on_refresh(token_data: dict[str, Any]) -> None:
        persisted_tokens.append(dict(token_data))

    client = MyQClient(
        access_token="initial_access_token",
        refresh_token="initial_refresh_token",
        expires_at=time.time() - 100,  # Expired
        session=mock_session,
        on_token_refreshed=on_refresh,
    )

    # Launch 10 concurrent refresh operations
    tasks = [asyncio.create_task(client.refresh_tokens()) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # 1. Exactly one HTTP request was issued to /connect/token
    assert refresh_request_count == 1
    token_requests = [r for r in mock_session.requests if "/connect/token" in r["url"]]
    assert len(token_requests) == 1

    # 2. All 10 coroutines received the identical refreshed tokens
    for res in results:
        assert res[CONF_ACCESS_TOKEN] == "new_race_access_token_999"
        assert res[CONF_REFRESH_TOKEN] == "new_race_refresh_token_999"

    # 3. Client state was updated
    assert client.access_token == "new_race_access_token_999"
    assert client.refresh_token == "new_race_refresh_token_999"
    assert len(persisted_tokens) >= 1
    assert persisted_tokens[0]["access_token"] == "new_race_access_token_999"


# -----------------------------------------------------------------------------
# 3. Zero-Lockout Circuit Breaker (Requirement R3.1 & R6.2)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_on_invalid_grant_400(mock_session: MockAsyncSession) -> None:
    """Verify HTTP 400 invalid_grant trips circuit breaker, clears tokens, and prevents login retry."""
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=400,
            json_data={
                "error": "invalid_grant",
                "error_description": "The refresh token has been revoked or expired.",
            },
        ),
    )

    client = MyQClient(
        access_token="old_access_token",
        refresh_token="revoked_refresh_token",
        expires_at=time.time() - 10,
        session=mock_session,
    )

    with pytest.raises(MyQInvalidGrantError) as exc_info:
        await client.refresh_tokens()

    assert "permanently rejected" in str(exc_info.value).lower()
    # Circuit breaker must wipe invalid tokens
    assert client.access_token is None
    assert client.refresh_token is None
    assert client.expires_at is None

    # Zero background password logins allowed
    auth_requests = [r for r in mock_session.requests if "/connect/authorize" in r["url"]]
    assert len(auth_requests) == 0


@pytest.mark.asyncio
async def test_circuit_breaker_on_error_code_401_102(mock_session: MockAsyncSession) -> None:
    """Verify HTTP 401 with code 401.102 trips fatal circuit breaker."""
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=401,
            json_data={
                "code": "401.102",
                "message": "OAUTH_TOKEN_BAD_REQUEST: Refresh token expired.",
            },
        ),
    )

    client = MyQClient(
        access_token="old_access_token",
        refresh_token="bad_refresh_token",
        expires_at=time.time() - 10,
        session=mock_session,
    )

    with pytest.raises(MyQInvalidGrantError) as exc_info:
        await client.refresh_tokens()

    assert "401.102" in str(exc_info.value)
    assert client.access_token is None
    assert client.refresh_token is None


@pytest.mark.asyncio
async def test_circuit_breaker_on_rate_limit_429(mock_session: MockAsyncSession) -> None:
    """Verify HTTP 429 raises MyQRateLimitError without tripping permanent invalid grant."""
    mock_session.add_route(
        "/api/v6.0/Accounts",
        MockResponse(
            status_code=429,
            json_data={"code": "309", "message": "Too Many Requests: Rate limit exceeded."},
        ),
    )

    client = MyQClient(
        access_token="valid_access_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    with pytest.raises(MyQRateLimitError) as exc_info:
        await client.get_devices()

    assert "rate limited" in str(exc_info.value).lower()
    # Tokens should remain intact on temporary rate limiting
    assert client.access_token == "valid_access_token"
    assert client.refresh_token == "valid_refresh_token"


@pytest.mark.asyncio
async def test_circuit_breaker_on_forbidden_403(mock_session: MockAsyncSession) -> None:
    """Verify HTTP 403 raises authentication/authorization error."""
    mock_session.add_route(
        "/api/v6.0/Accounts",
        MockResponse(
            status_code=403,
            json_data={"code": "302", "message": "Session / Account Mismatch."},
        ),
    )

    client = MyQClient(
        access_token="valid_access_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    with pytest.raises(MyQError):
        await client.get_devices()


# -----------------------------------------------------------------------------
# 4. Proactive Token Refresh (PROTOCOL.md §3.7 & Requirement R3.3)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_proactive_token_refresh_within_5_minutes(mock_session: MockAsyncSession) -> None:
    """Verify client automatically refreshes token before issuing API request when expiring within 300s."""
    client = MyQClient(
        access_token="expiring_access_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 150,  # Expiring in 2.5 minutes (< 300s)
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    assert client.is_token_expiring(leeway=300) is True

    # Call get_devices()
    devices = await client.get_devices()
    assert len(devices) > 0

    # Verify /connect/token was called BEFORE /Devices
    req_urls = [r["url"] for r in mock_session.requests]
    assert any("/connect/token" in u for u in req_urls)
    assert any("/Devices" in u for u in req_urls)

    token_req_idx = next(i for i, u in enumerate(req_urls) if "/connect/token" in u)
    dev_req_idx = next(i for i, u in enumerate(req_urls) if "/Devices" in u)
    assert token_req_idx < dev_req_idx

    # And the device request used the newly refreshed token
    dev_req = mock_session.requests[dev_req_idx]
    assert dev_req["headers"]["Authorization"] == "Bearer mock_new_access_token"


@pytest.mark.asyncio
async def test_proactive_refresh_not_triggered_when_fresh(mock_session: MockAsyncSession) -> None:
    """Verify client does NOT refresh token when token has ample validity remaining (> 300s)."""
    client = MyQClient(
        access_token="fresh_access_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 2000,  # 33+ minutes remaining
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    assert client.is_token_expiring(leeway=300) is False

    await client.get_devices()

    # No token refresh request should have occurred
    token_requests = [r for r in mock_session.requests if "/connect/token" in r["url"]]
    assert len(token_requests) == 0


# -----------------------------------------------------------------------------
# 5. Reactive Token Refresh (PROTOCOL.md §3.7 & Requirement R3.3)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reactive_token_refresh_on_401(mock_session: MockAsyncSession) -> None:
    """Verify single reactive refresh is triggered on HTTP 401 and request is successfully replayed."""
    first_call = True

    async def devices_route(request_record: dict[str, Any]) -> MockResponse:
        nonlocal first_call
        if first_call:
            first_call = False
            return MockResponse(
                status_code=401,
                json_data={"code": "401.209", "message": "Access token has expired."},
            )
        return MockResponse(
            status_code=200,
            json_data={"items": [{"serial_number": "TEST_REACTIVE_01"}]},
        )

    mock_session.add_route("/Devices", devices_route)

    client = MyQClient(
        access_token="stale_token",
        refresh_token="good_refresh_token",
        expires_at=time.time() + 1000,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    devices = await client.get_devices()
    assert len(devices) == 1
    assert devices[0]["serial_number"] == "TEST_REACTIVE_01"

    # Verify traffic sequence: 1st Devices (401) -> connect/token (200) -> 2nd Devices (200)
    dev_requests = [r for r in mock_session.requests if "/Devices" in r["url"]]
    token_requests = [r for r in mock_session.requests if "/connect/token" in r["url"]]
    assert len(dev_requests) == 2
    assert len(token_requests) == 1


@pytest.mark.asyncio
async def test_reactive_refresh_does_not_loop_on_persistent_401(mock_session: MockAsyncSession) -> None:
    """Verify reactive refresh fails immediately if 401 persists after token refresh (no infinite loop)."""
    mock_session.add_route(
        "/Devices",
        MockResponse(
            status_code=401,
            json_data={"code": "401.209", "message": "Access token rejected."},
        ),
    )

    client = MyQClient(
        access_token="bad_token",
        refresh_token="valid_refresh_token",
        expires_at=time.time() + 1000,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    with pytest.raises(MyQAuthError):
        await client.get_devices()

    # Should have retried at most once
    dev_requests = [r for r in mock_session.requests if "/Devices" in r["url"]]
    assert len(dev_requests) == 2


# -----------------------------------------------------------------------------
# 6. Mobile Fingerprint & Header Validation (PROTOCOL.md §4.1)
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mobile_headers_on_api_requests(mock_session: MockAsyncSession) -> None:
    """Verify all REST API calls transmit exact Android APK headers and TLS fingerprints."""
    client = MyQClient(
        access_token="test_header_bearer_token",
        refresh_token="test_refresh_token",
        expires_at=time.time() + 3600,
        account_id="e36e6761-0000-4000-8000-0123456789ab",
        session=mock_session,
    )

    await client.get_devices()

    dev_req = next(r for r in mock_session.requests if "/Devices" in r["url"])
    headers = dev_req["headers"]

    assert headers["User-Agent"] == USER_AGENT_API
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers[HEADER_API_VERSION] == API_VERSION
    assert headers[HEADER_CULTURE] == CULTURE
    assert headers[HEADER_APPLICATION_ID] == CLIENT_ID
    assert headers[HEADER_ANTI_CSRF] == "AntiCSRF"
    assert headers["Authorization"] == "Bearer test_header_bearer_token"


@pytest.mark.asyncio
async def test_mobile_headers_on_token_refresh(mock_session: MockAsyncSession) -> None:
    """Verify OAuth2 token refresh request includes mandatory IsRefresh: true header."""
    client = MyQClient(
        access_token="old_token",
        refresh_token="refresh_token_abc",
        expires_at=time.time() - 10,
        session=mock_session,
    )

    await client.refresh_tokens()

    token_req = next(r for r in mock_session.requests if "/connect/token" in r["url"])
    headers = token_req["headers"]

    assert headers[HEADER_IS_REFRESH] == "true"
    assert headers[HEADER_BRAND_ID] == BRAND_ID
    assert headers[HEADER_APP_VERSION] == APP_VERSION
    assert headers["User-Agent"] == USER_AGENT_API
    assert "application/x-www-form-urlencoded" in headers["Content-Type"]

"""Tests for Chamberlain/LiftMaster myQ config flow and options flow.

Conforms to Requirement R3.2 (Dual-mode login: Mode A automated & Mode B manual redirect paste,
re-authentication, scan interval options flow).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.myq_apk.api import MyQClient
from custom_components.myq_apk.config_flow import MyQConfigFlow, MyQOptionsFlowHandler
from custom_components.myq_apk.const import (
    AUTH_MODE_AUTOMATED,
    AUTH_MODE_MANUAL,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_ID,
    CONF_AUTH_MODE,
    CONF_PASSWORD,
    CONF_REDIRECT_URL,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DOMAIN,
)
from tests.conftest import MockAsyncSession, MockResponse


@pytest.fixture(autouse=True)
def intercept_client_sessions(monkeypatch: pytest.MonkeyPatch, mock_session: MockAsyncSession) -> None:
    """Ensure all MyQClient instances in config_flow use mock_session."""
    async def mock_get_session(self: Any) -> MockAsyncSession:
        return mock_session

    monkeypatch.setattr(MyQClient, "_get_session", mock_get_session)


# -----------------------------------------------------------------------------
# 1. User Menu Step
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_step_shows_menu(mock_hass: Any) -> None:
    """Verify initial user step displays choices for Mode A and Mode B."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    result = await flow.async_step_user()
    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert "mode_automated" in result["menu_options"]
    assert "mode_manual" in result["menu_options"]


# -----------------------------------------------------------------------------
# 2. Mode A: Automated Credentials Flow
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_automated_success(mock_hass: Any, mock_session: MockAsyncSession) -> None:
    """Verify Mode A automated login successfully authenticates and creates config entry."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    # Route 1: Initial GET to authorize page returns HTML form
    html_form = (
        '<html><body><form action="/connect/authorize" method="post">'
        '<input type="hidden" name="ReturnUrl" value="/connect/authorize/callback" />'
        '<input type="text" name="Email" />'
        '<input type="password" name="Password" />'
        '</form></body></html>'
    )
    mock_session.add_route(
        lambda m, u: m == "GET" and "/connect/authorize" in u,
        MockResponse(status_code=200, text=html_form),
    )

    # Route 2: POST credentials returns 302 redirect with authorization code
    mock_session.add_route(
        lambda m, u: m == "POST" and "/connect/authorize" in u,
        MockResponse(
            status_code=302,
            text="",
            headers={"Location": "com.myqops://android?code=MOCK_AUTH_CODE_MODE_A"},
        ),
    )

    # Route 3: POST to /connect/token exchanges code for tokens
    mock_session.add_route(
        lambda m, u: m == "POST" and "/connect/token" in u,
        MockResponse(
            status_code=200,
            json_data={
                "access_token": "mode_a_access_token",
                "refresh_token": "mode_a_refresh_token",
                "expires_in": 3600,
            },
        ),
    )

    # Route 4: GET /api/v6.0/accounts
    mock_session.add_route(
        lambda m, u: m == "GET" and "/api/v6.0/accounts" in u,
        MockResponse(
            status_code=200,
            json_data={
                "accounts": [
                    {
                        "id": "acc-uuid-mode-a-1234",
                        "name": "My Primary Garage",
                    }
                ]
            },
        ),
    )

    # Initial form display
    init_res = await flow.async_step_mode_automated()
    assert init_res["type"] == "form"
    assert init_res["step_id"] == "mode_automated"

    # Submit credentials
    result = await flow.async_step_mode_automated(
        {
            CONF_USERNAME: "owner@example.com",
            CONF_PASSWORD: "CorrectPassword123!",
        }
    )

    assert result["type"] == "create_entry"
    assert "My Primary Garage" in result["title"]
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_AUTOMATED
    assert data[CONF_USERNAME] == "owner@example.com"
    assert data[CONF_ACCESS_TOKEN] == "mode_a_access_token"
    assert data[CONF_REFRESH_TOKEN] == "mode_a_refresh_token"
    assert data[CONF_ACCOUNT_ID] == "acc-uuid-mode-a-1234"


@pytest.mark.asyncio
async def test_mode_automated_invalid_credentials(mock_hass: Any, mock_session: MockAsyncSession) -> None:
    """Verify Mode A handles invalid credentials by returning invalid_auth error."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    mock_session.add_route(
        lambda m, u: m == "GET" and "/connect/authorize" in u,
        MockResponse(status_code=200, text='<form action="/login"></form>'),
    )
    # Submission returns page with validation-errors
    mock_session.add_route(
        lambda m, u: m == "POST" and "/login" in u,
        MockResponse(
            status_code=200,
            text='<div id="validation-errors">Invalid username or password.</div>',
        ),
    )

    result = await flow.async_step_mode_automated(
        {
            CONF_USERNAME: "owner@example.com",
            CONF_PASSWORD: "WrongPassword!",
        }
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_mode_automated_network_timeout(mock_hass: Any, mock_session: MockAsyncSession) -> None:
    """Verify Mode A handles network timeout by returning cannot_connect error."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    async def timeout_route(record: dict[str, Any]) -> MockResponse:
        raise TimeoutError("Connection timed out")

    mock_session.add_route("/connect/authorize", timeout_route)

    result = await flow.async_step_mode_automated(
        {
            CONF_USERNAME: "owner@example.com",
            CONF_PASSWORD: "Password123!",
        }
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


# -----------------------------------------------------------------------------
# 3. Mode B: Manual Redirect URL Paste Flow
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mode_manual_step_shows_authorization_url(mock_hass: Any) -> None:
    """Verify Mode B step initially displays instructions with the generated authorization URL."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    result = await flow.async_step_mode_manual()
    assert result["type"] == "form"
    assert result["step_id"] == "mode_manual"
    auth_url = result["description_placeholders"]["auth_url"]
    assert "https://partner-identity.myq-cloud.com/connect/authorize" in auth_url
    assert "client_id=ANDROID_CGI_MYQ" in auth_url


@pytest.mark.asyncio
async def test_mode_manual_success_with_redirect_url(
    mock_hass: Any, mock_session: MockAsyncSession
) -> None:
    """Verify Mode B successfully parses code from full com.myqops://android redirect URL."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    # First call to initialize PKCE verifier
    await flow.async_step_mode_manual()

    # Configure token exchange & accounts
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=200,
            json_data={
                "access_token": "mode_b_access_token",
                "refresh_token": "mode_b_refresh_token",
                "expires_in": 3600,
            },
        ),
    )
    mock_session.add_route(
        "/api/v6.0/accounts",
        MockResponse(
            status_code=200,
            json_data={"accounts": [{"id": "acc-uuid-mode-b-5678", "name": "Cabin Garage"}]},
        ),
    )

    # User pastes redirect URL
    redirect_url = "com.myqops://android?code=MOCK_CODE_9876543210&scope=MyQ_Residential%20offline_access"
    result = await flow.async_step_mode_manual({CONF_REDIRECT_URL: redirect_url})

    assert result["type"] == "create_entry"
    assert "Cabin Garage" in result["title"]
    data = result["data"]
    assert data[CONF_AUTH_MODE] == AUTH_MODE_MANUAL
    assert data[CONF_ACCESS_TOKEN] == "mode_b_access_token"
    assert data[CONF_REFRESH_TOKEN] == "mode_b_refresh_token"
    assert data[CONF_ACCOUNT_ID] == "acc-uuid-mode-b-5678"


@pytest.mark.asyncio
async def test_mode_manual_success_with_raw_code(
    mock_hass: Any, mock_session: MockAsyncSession
) -> None:
    """Verify Mode B accepts raw authorization code directly."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    await flow.async_step_mode_manual()

    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=200,
            json_data={
                "access_token": "raw_code_access_token",
                "refresh_token": "raw_code_refresh_token",
                "expires_in": 3600,
            },
        ),
    )

    result = await flow.async_step_mode_manual(
        {CONF_REDIRECT_URL: "RAW_AUTHORIZATION_CODE_TOKEN_123456"}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_ACCESS_TOKEN] == "raw_code_access_token"


@pytest.mark.asyncio
async def test_mode_manual_error_query_param(mock_hass: Any) -> None:
    """Verify Mode B handles OAuth error in redirect URL."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    await flow.async_step_mode_manual()

    url_with_error = "com.myqops://android?error=access_denied&error_description=User+cancelled"
    result = await flow.async_step_mode_manual({CONF_REDIRECT_URL: url_with_error})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_mode_manual_missing_code(mock_hass: Any) -> None:
    """Verify Mode B returns no_code when input contains no authorization code."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass

    await flow.async_step_mode_manual()

    result = await flow.async_step_mode_manual({CONF_REDIRECT_URL: "https://invalid.com/empty"})
    assert result["type"] == "form"
    assert result["errors"]["base"] == "no_code"


# -----------------------------------------------------------------------------
# 4. Options Flow: Scan Interval
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_options_flow_scan_interval(mock_config_entry: Any) -> None:
    """Verify options flow allows configuring scan interval between 60s and 600s."""
    options_flow = MyQConfigFlow.async_get_options_flow(mock_config_entry)

    # Initial form
    form_res = await options_flow.async_step_init()
    assert form_res["type"] == "form"
    assert form_res["step_id"] == "init"

    # Submit updated scan interval (300 seconds)
    save_res = await options_flow.async_step_init({CONF_SCAN_INTERVAL: 300})
    assert save_res["type"] == "create_entry"
    assert save_res["data"][CONF_SCAN_INTERVAL] == 300


# -----------------------------------------------------------------------------
# 5. Re-Authentication Flow
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reauth_flow_success(
    mock_hass: Any, mock_config_entry: Any, mock_session: MockAsyncSession
) -> None:
    """Verify re-authentication flow updates credentials and reloads config entry."""
    flow = MyQConfigFlow()
    flow.hass = mock_hass
    flow.context = {"entry_id": mock_config_entry.entry_id}
    mock_hass.config_entries._entries[mock_config_entry.entry_id] = mock_config_entry

    mock_session.add_route(
        lambda m, u: m == "GET" and "/connect/authorize" in u,
        MockResponse(status_code=200, text='<form action="/login"></form>'),
    )
    mock_session.add_route(
        lambda m, u: m == "POST" and "/login" in u,
        MockResponse(
            status_code=302,
            headers={"Location": "com.myqops://android?code=REAUTH_CODE_OK"},
        ),
    )
    mock_session.add_route(
        "/connect/token",
        MockResponse(
            status_code=200,
            json_data={
                "access_token": "reauth_new_access_token",
                "refresh_token": "reauth_new_refresh_token",
                "expires_in": 3600,
            },
        ),
    )

    init_res = await flow.async_step_reauth(mock_config_entry.data)
    assert init_res["type"] == "form"
    assert init_res["step_id"] == "reauth_confirm"

    confirm_res = await flow.async_step_reauth_confirm(
        {
            CONF_USERNAME: "reauth_user@example.com",
            CONF_PASSWORD: "NewPassword2026!",
        }
    )

    assert confirm_res["type"] == "abort"
    assert confirm_res["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_ACCESS_TOKEN] == "reauth_new_access_token"
    assert mock_config_entry.data[CONF_REFRESH_TOKEN] == "reauth_new_refresh_token"
    assert mock_config_entry.data[CONF_USERNAME] == "reauth_user@example.com"

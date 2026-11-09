"""Core myQ API Client with Anti-Lockout OAuth2 Architecture and TLS Fingerprinting.

Conforms to Requirement R3 (Anti-Lockout & Self-Healing Auth Architecture) and
authoritative specs in docs/PROTOCOL.md. Extracted offline from Android APK
com.chamberlain.android.liftmaster.myq (v5.323.0.1132341).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from html.parser import HTMLParser
import inspect
import json as json_module
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

try:
    from curl_cffi.requests import AsyncSession, Response
    from curl_cffi.requests.errors import RequestsError, Timeout as CurlTimeout
except ImportError:
    AsyncSession = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]
    RequestsError = Exception  # type: ignore[assignment, misc]
    CurlTimeout = TimeoutError  # type: ignore[assignment, misc]

try:
    from .const import (
        ACR_VALUES,
        API_BASE_URL,
        API_VERSION,
        APP_VERSION,
        AUTHORIZE_URL,
        BRAND_ID,
        CLIENT_ID,
        CODE_CHALLENGE_METHOD,
        COMMAND_TIMEOUT,
        CONF_ACCESS_TOKEN,
        CONF_ACCOUNT_ID,
        CONF_EXPIRES_AT,
        CONF_REFRESH_TOKEN,
        CULTURE,
        DEFAULT_SCOPE,
        DEFAULT_TOKEN_LIFESPAN,
        DEVICE_FAMILY_GARAGE_DOOR,
        DEVICE_FAMILY_GATEWAY,
        DEVICE_FAMILY_LAMP,
        HEADER_ANTI_CSRF,
        HEADER_APP_VERSION,
        HEADER_APPLICATION_ID,
        HEADER_API_VERSION,
        HEADER_BRAND_ID,
        HEADER_CULTURE,
        HEADER_IS_REFRESH,
        PROMPT,
        REDIRECT_URI,
        RESPONSE_TYPE,
        STATE_CLOSED,
        STATE_OPEN,
        STATE_UNKNOWN,
        TOKEN_EXPIRATION_LEEWAY,
        TOKEN_URL,
        UI_LOCALES,
        USER_AGENT_API,
        USER_AGENT_WEB,
        MyQAuthError,
        MyQCommandError,
        MyQDeviceNotFoundError,
        MyQError,
        MyQInvalidGrantError,
        MyQOfflineError,
        MyQRateLimitError,
        MyQTimeoutError,
    )
except ImportError:
    from const import (  # type: ignore[no-redef]
        ACR_VALUES,
        API_BASE_URL,
        API_VERSION,
        APP_VERSION,
        AUTHORIZE_URL,
        BRAND_ID,
        CLIENT_ID,
        CODE_CHALLENGE_METHOD,
        COMMAND_TIMEOUT,
        CONF_ACCESS_TOKEN,
        CONF_ACCOUNT_ID,
        CONF_EXPIRES_AT,
        CONF_REFRESH_TOKEN,
        CULTURE,
        DEFAULT_SCOPE,
        DEFAULT_TOKEN_LIFESPAN,
        DEVICE_FAMILY_GARAGE_DOOR,
        DEVICE_FAMILY_GATEWAY,
        DEVICE_FAMILY_LAMP,
        HEADER_ANTI_CSRF,
        HEADER_APP_VERSION,
        HEADER_APPLICATION_ID,
        HEADER_API_VERSION,
        HEADER_BRAND_ID,
        HEADER_CULTURE,
        HEADER_IS_REFRESH,
        PROMPT,
        REDIRECT_URI,
        RESPONSE_TYPE,
        STATE_CLOSED,
        STATE_OPEN,
        STATE_UNKNOWN,
        TOKEN_EXPIRATION_LEEWAY,
        TOKEN_URL,
        UI_LOCALES,
        USER_AGENT_API,
        USER_AGENT_WEB,
        MyQAuthError,
        MyQCommandError,
        MyQDeviceNotFoundError,
        MyQError,
        MyQInvalidGrantError,
        MyQOfflineError,
        MyQRateLimitError,
        MyQTimeoutError,
    )

_LOGGER = logging.getLogger(__name__)


class _LoginFormParser(HTMLParser):
    """HTML parser to extract IdentityServer login form action and input fields."""

    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.inputs: dict[str, str] = {}
        self.in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self.in_form = True
            if "action" in attr_dict:
                self.action = attr_dict["action"]
        elif tag == "input":
            name = attr_dict.get("name")
            value = attr_dict.get("value", "")
            if name:
                self.inputs[name] = value


class MyQClient:
    """myQ API Client enforcing anti-lockout safeguards and physical safety rules."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        expires_at: Optional[float] = None,
        account_id: Optional[str] = None,
        session: Optional[Any] = None,
        on_token_refreshed: Optional[Callable[[dict[str, Any]], Any]] = None,
    ) -> None:
        """Initialize the myQ client.

        Args:
            access_token: Stored OAuth2 bearer access token.
            refresh_token: Stored rolling refresh token.
            expires_at: POSIX epoch timestamp of access token expiry.
            account_id: Cached primary account ID UUID.
            session: Optional custom or mock HTTP session (duck-typed or curl_cffi AsyncSession).
            on_token_refreshed: Callback invoked immediately when rolling tokens are refreshed.
        """
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.account_id = account_id
        self._session = session
        self._owns_session = session is None
        self.on_token_refreshed = on_token_refreshed

        # Single-flight lock guarding rolling token refresh
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_time: float = 0.0

        # Memory caches to prevent redundant endpoint queries
        self._accounts_cache: Optional[list[dict[str, Any]]] = None
        self._devices_cache: dict[str, dict[str, Any]] = {}
        self._code_verifier: Optional[str] = None

    async def __aenter__(self) -> "MyQClient":
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Close internal HTTP session if owned by this client instance."""
        if self._owns_session and self._session is not None:
            if hasattr(self._session, "close"):
                res = self._session.close()
                if inspect.isawaitable(res):
                    await res
            self._session = None

    def clear_cache(self) -> None:
        """Clear cached account and device structures."""
        self._accounts_cache = None
        self._devices_cache.clear()

    # -------------------------------------------------------------------------
    # Cryptographic PKCE & URL Builders (PROTOCOL.md §3.2 & §3.3)
    # -------------------------------------------------------------------------
    @staticmethod
    def generate_pkce() -> tuple[str, str]:
        """Generate (code_verifier, code_challenge) using RFC 7636 S256.

        - code_verifier: 32 cryptographically secure random bytes, Base64 URL-safe unpadded (43 chars).
        - code_challenge: SHA-256 hash of verifier, Base64 URL-safe unpadded.
        """
        rand_bytes = secrets.token_bytes(32)
        code_verifier = base64.urlsafe_b64encode(rand_bytes).decode("ascii").rstrip("=")
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return code_verifier, code_challenge

    def get_authorization_url(self, code_challenge: Optional[str] = None) -> str:
        """Construct the official OAuth2 authorization URL.

        If code_challenge is omitted, a new PKCE pair is generated and saved internally.
        """
        if code_challenge is None:
            self._code_verifier, code_challenge = self.generate_pkce()

        params = {
            "client_id": CLIENT_ID,
            "response_type": RESPONSE_TYPE,
            "redirect_uri": REDIRECT_URI,
            "scope": DEFAULT_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": CODE_CHALLENGE_METHOD,
            "ui_locales": UI_LOCALES,
            "prompt": PROMPT,
            "acr_values": ACR_VALUES,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    @staticmethod
    def _extract_code_from_url(url_or_string: str) -> Optional[str]:
        """Extract authorization code from redirect URL or raw string."""
        if not url_or_string:
            return None

        # Check URL query params
        parsed = urlparse(url_or_string)
        if parsed.query:
            qp = parse_qs(parsed.query)
            if "code" in qp and qp["code"]:
                return qp["code"][0]

        # Check raw query string fragment without scheme
        if "?" in url_or_string:
            qs = url_or_string.split("?", 1)[1]
            qp = parse_qs(qs)
            if "code" in qp and qp["code"]:
                return qp["code"][0]

        # Fallback: check if raw authorization code was provided directly
        cleaned = url_or_string.strip()
        if (
            " " not in cleaned
            and "=" not in cleaned
            and "/" not in cleaned
            and len(cleaned) >= 16
        ):
            return cleaned

        return None

    # -------------------------------------------------------------------------
    # Authentication & Token Management (Requirement R3 & PROTOCOL.md §3)
    # -------------------------------------------------------------------------
    def is_token_expiring(self, leeway: float = TOKEN_EXPIRATION_LEEWAY) -> bool:
        """Return True if token is missing, expired, or will expire within leeway seconds."""
        if not self.access_token or self.expires_at is None:
            return True
        return time.time() >= (self.expires_at - leeway)

    def get_token_data(self) -> dict[str, Any]:
        """Return current token data dictionary for persistence."""
        return {
            CONF_ACCESS_TOKEN: self.access_token,
            CONF_REFRESH_TOKEN: self.refresh_token,
            CONF_EXPIRES_AT: self.expires_at,
            CONF_ACCOUNT_ID: self.account_id,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
        }

    async def _notify_token_refreshed(self, token_data: dict[str, Any]) -> None:
        """Atomically invoke rolling token refresh callback if configured."""
        if self.on_token_refreshed is not None:
            try:
                res = self.on_token_refreshed(token_data)
                if inspect.isawaitable(res):
                    await res
            except Exception as exc:
                _LOGGER.exception("Error in on_token_refreshed persistence callback: %s", exc)

    async def _apply_token_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Update internal rolling tokens and invoke persistence callback."""
        access_token = response_data.get("access_token")
        if not access_token:
            raise MyQAuthError("Token exchange response missing 'access_token'")

        self.access_token = str(access_token)
        if "refresh_token" in response_data:
            self.refresh_token = str(response_data["refresh_token"])

        expires_in = float(response_data.get("expires_in", DEFAULT_TOKEN_LIFESPAN))
        self.expires_at = time.time() + expires_in
        self._last_refresh_time = time.time()

        token_data = self.get_token_data()
        token_data["expires_in"] = expires_in

        await self._notify_token_refreshed(token_data)
        return token_data

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange authorization code and PKCE verifier for OAuth2 tokens."""
        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": code_verifier,
            "scope": DEFAULT_SCOPE,
        }
        encoded_data = urlencode(payload)
        headers = {
            "User-Agent": USER_AGENT_API,
            HEADER_BRAND_ID: BRAND_ID,
            HEADER_APP_VERSION: APP_VERSION,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        _LOGGER.debug("Exchanging authorization code at %s", TOKEN_URL)
        status_code, parsed_json, text, _ = await self._send_http_request(
            method="POST",
            url=TOKEN_URL,
            headers=headers,
            data=encoded_data,
            timeout=COMMAND_TIMEOUT,
        )

        if status_code != 200 or not isinstance(parsed_json, dict):
            raise self._parse_error(status_code, parsed_json, text)

        return await self._apply_token_response(parsed_json)

    async def refresh_tokens(self, force: bool = False) -> dict[str, Any]:
        """Refresh rolling tokens protected by single-flight lock.

        Enforces:
        - Single-flight lock: concurrent callers wait for single in-flight refresh.
        - Atomic rolling update: previous refresh token is immediately invalidated by server.
        - Zero-lockout circuit breaker: on invalid_grant, immediately raise MyQInvalidGrantError
          with zero background password retries.
        """
        if not self.refresh_token:
            raise MyQAuthError("Cannot refresh tokens: No refresh token available")

        current_access_token = self.access_token
        current_refresh_timestamp = self._last_refresh_time

        async with self._refresh_lock:
            # Check if another coroutine just completed a refresh while we waited for the lock
            if not force:
                if (
                    self._last_refresh_time > current_refresh_timestamp
                    and not self.is_token_expiring(leeway=TOKEN_EXPIRATION_LEEWAY)
                ):
                    _LOGGER.debug("Token refreshed while waiting for lock; skipping redundant refresh")
                    return self.get_token_data()
                if not self.is_token_expiring(leeway=TOKEN_EXPIRATION_LEEWAY) and self.access_token:
                    return self.get_token_data()
            else:
                # Force refresh: check if token was updated during lock wait
                if (
                    self._last_refresh_time > current_refresh_timestamp
                    and self.access_token != current_access_token
                ):
                    _LOGGER.debug("Token updated during reactive lock wait; skipping redundant refresh")
                    return self.get_token_data()

            payload = {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": DEFAULT_SCOPE,
            }
            encoded_data = urlencode(payload)
            headers = {
                "User-Agent": USER_AGENT_API,
                HEADER_BRAND_ID: BRAND_ID,
                HEADER_APP_VERSION: APP_VERSION,
                HEADER_IS_REFRESH: "true",
                "Content-Type": "application/x-www-form-urlencoded",
            }

            _LOGGER.debug("Issuing single-flight OAuth2 refresh request to %s", TOKEN_URL)
            status_code, parsed_json, text, _ = await self._send_http_request(
                method="POST",
                url=TOKEN_URL,
                headers=headers,
                data=encoded_data,
                timeout=COMMAND_TIMEOUT,
            )

            # Permanent rejection circuit breaker (PROTOCOL.md §3.7)
            if status_code in (400, 401):
                err_type = ""
                err_code = ""
                err_desc = ""
                if isinstance(parsed_json, dict):
                    err_type = str(parsed_json.get("error", ""))
                    err_code = str(parsed_json.get("code", ""))
                    err_desc = str(
                        parsed_json.get("error_description", parsed_json.get("message", ""))
                    )

                if (
                    err_type == "invalid_grant"
                    or err_code == "401.102"
                    or "invalid_grant" in text.lower()
                ):
                    _LOGGER.error(
                        "OAuth refresh token permanently rejected (%s / %s): %s. Tripping circuit breaker.",
                        err_type,
                        err_code,
                        err_desc,
                    )
                    # Clear invalid tokens to prevent further refresh loops
                    self.access_token = None
                    self.refresh_token = None
                    self.expires_at = None
                    raise MyQInvalidGrantError(
                        f"OAuth refresh token permanently rejected ({err_type or err_code}): "
                        f"{err_desc or text}. Re-authentication required."
                    )

            if status_code != 200 or not isinstance(parsed_json, dict):
                raise self._parse_error(status_code, parsed_json, text)

            return await self._apply_token_response(parsed_json)

    # -------------------------------------------------------------------------
    # Authentication Modes (Requirement R3.2)
    # -------------------------------------------------------------------------
    async def authenticate(self, username: str, password: str) -> dict[str, Any]:
        """Execute Mode A automated OAuth2 PKCE login flow."""
        code_verifier, code_challenge = self.generate_pkce()
        self._code_verifier = code_verifier
        auth_url = self.get_authorization_url(code_challenge)

        _LOGGER.debug("Mode A: Requesting authorization page: %s", auth_url)
        headers = {
            "User-Agent": USER_AGENT_WEB,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        status_code, _, text, resp_headers = await self._send_http_request(
            method="GET",
            url=auth_url,
            headers=headers,
            timeout=COMMAND_TIMEOUT,
            allow_redirects=True,
        )

        # Check for immediate redirect location
        location = resp_headers.get("Location") or resp_headers.get("location", "")
        code = self._extract_code_from_url(location)
        if code:
            return await self.exchange_code(code, code_verifier)

        # Parse login form HTML
        parser = _LoginFormParser()
        parser.feed(text)

        form_action = parser.action or auth_url
        if not form_action.startswith("http://") and not form_action.startswith("https://"):
            form_action = urljoin(auth_url, form_action)

        form_data = dict(parser.inputs)
        form_data["Email"] = username
        form_data["Password"] = password

        post_headers = {
            "User-Agent": USER_AGENT_WEB,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": auth_url,
        }

        _LOGGER.debug("Mode A: Submitting credentials to %s", form_action)
        status_code, post_json, post_text, post_resp_headers = await self._send_http_request(
            method="POST",
            url=form_action,
            headers=post_headers,
            data=urlencode(form_data),
            timeout=COMMAND_TIMEOUT,
            allow_redirects=False,
        )

        # Check redirect location header
        redirect_loc = post_resp_headers.get("Location") or post_resp_headers.get("location", "")
        code = self._extract_code_from_url(redirect_loc)

        # Follow intermediate redirect if necessary
        if not code and (300 <= status_code < 400) and redirect_loc:
            if redirect_loc.startswith("http://") or redirect_loc.startswith("https://"):
                _, _, _, step_headers = await self._send_http_request(
                    method="GET",
                    url=redirect_loc,
                    headers={"User-Agent": USER_AGENT_WEB},
                    allow_redirects=False,
                )
                step_loc = step_headers.get("Location") or step_headers.get("location", "")
                code = self._extract_code_from_url(step_loc)

        if not code:
            if "validation-errors" in post_text or "invalid" in post_text.lower():
                raise MyQAuthError("Invalid username or password")
            if "email-not-approved" in post_text:
                raise MyQAuthError("Account email has not been approved or verified")
            raise MyQAuthError(
                f"Automated login failed to produce authorization code (HTTP {status_code})"
            )

        return await self.exchange_code(code, code_verifier)

    async def authenticate_with_redirect_url(
        self, redirect_url: str, code_verifier: Optional[str] = None
    ) -> dict[str, Any]:
        """Execute Mode B manual code exchange using pasted redirect URL."""
        verifier = code_verifier or self._code_verifier
        if not verifier:
            raise MyQAuthError(
                "Missing PKCE code_verifier. Generate authorization URL first or supply code_verifier."
            )

        # Check for error query parameters
        parsed = urlparse(redirect_url)
        query_params = parse_qs(parsed.query)
        if "error" in query_params:
            err = query_params["error"][0]
            err_desc = query_params.get("error_description", [""])[0]
            raise MyQAuthError(f"OAuth authorization failed: {err} ({err_desc})")

        code = self._extract_code_from_url(redirect_url)
        if not code:
            raise MyQAuthError("No authorization code found in provided redirect URL")

        return await self.exchange_code(code, verifier)

    # -------------------------------------------------------------------------
    # HTTP Transport Engine & Error Mapping (PROTOCOL.md §4 & §9)
    # -------------------------------------------------------------------------
    async def _get_session(self) -> Any:
        """Get or lazily instantiate curl_cffi AsyncSession with TLS impersonation."""
        if self._session is None:
            if AsyncSession is not None:
                self._session = AsyncSession(
                    impersonate="chrome131_android",
                    timeout=COMMAND_TIMEOUT,
                )
                self._owns_session = True
            else:
                raise MyQError("curl_cffi is required for mobile TLS impersonation")
        return self._session

    def _get_api_headers(self) -> dict[str, str]:
        """Construct exact mobile client request headers (PROTOCOL.md §4.1)."""
        headers = {
            "User-Agent": USER_AGENT_API,
            HEADER_BRAND_ID: BRAND_ID,
            HEADER_APP_VERSION: APP_VERSION,
            HEADER_API_VERSION: API_VERSION,
            HEADER_CULTURE: CULTURE,
            HEADER_APPLICATION_ID: CLIENT_ID,
            HEADER_ANTI_CSRF: "AntiCSRF",
            "Accept": "application/json",
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def _send_http_request(
        self,
        method: str,
        url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        timeout: float = COMMAND_TIMEOUT,
        allow_redirects: bool = True,
    ) -> tuple[int, Any, str, dict[str, str]]:
        """Send HTTP request via the session, returning (status_code, parsed_json, text, headers)."""
        session = await self._get_session()

        kwargs: dict[str, Any] = {
            "timeout": timeout,
        }
        if headers is not None:
            kwargs["headers"] = headers
        if params is not None:
            kwargs["params"] = params
        if data is not None:
            kwargs["data"] = data
        if json is not None:
            kwargs["json"] = json
        if not allow_redirects:
            kwargs["allow_redirects"] = False

        try:
            if hasattr(session, "request"):
                resp = await session.request(method, url, **kwargs)
            else:
                method_func = getattr(session, method.lower())
                resp = await method_func(url, **kwargs)

            # Extract status code
            status_code = getattr(resp, "status_code", getattr(resp, "status", 200))

            # Extract response headers
            resp_headers: dict[str, str] = {}
            raw_headers = getattr(resp, "headers", None)
            if raw_headers is not None:
                if hasattr(raw_headers, "items"):
                    resp_headers = {str(k): str(v) for k, v in raw_headers.items()}
                elif isinstance(raw_headers, dict):
                    resp_headers = {str(k): str(v) for k, v in raw_headers.items()}

            # Extract text
            text = ""
            if hasattr(resp, "text"):
                text = resp.text
                if inspect.isawaitable(text):
                    text = await text
            elif hasattr(resp, "content"):
                content = resp.content
                if inspect.isawaitable(content):
                    content = await content
                if isinstance(content, bytes):
                    text = content.decode("utf-8", errors="replace")

            # Extract json
            parsed_json = None
            if hasattr(resp, "json"):
                try:
                    parsed_json = resp.json()
                    if inspect.isawaitable(parsed_json):
                        parsed_json = await parsed_json
                except Exception:
                    parsed_json = None

            if parsed_json is None and text:
                try:
                    parsed_json = json_module.loads(text)
                except Exception:
                    pass

            return status_code, parsed_json, text, resp_headers

        except (asyncio.TimeoutError, TimeoutError, CurlTimeout) as exc:
            raise MyQTimeoutError(f"HTTP {method} request to {url} timed out after {timeout}s") from exc
        except (RequestsError, OSError) as exc:
            raise MyQOfflineError(f"Network error connecting to {url}: {exc}") from exc

    def _parse_error(self, status_code: int, data: Any, text: str) -> Exception:
        """Parse error payload from API response and return appropriate exception."""
        code = ""
        message = ""
        description = ""

        if isinstance(data, dict):
            # OAuth2 format
            if "error" in data:
                err_type = str(data["error"])
                err_desc = str(data.get("error_description", ""))
                if err_type == "invalid_grant" or "invalid" in err_type:
                    return MyQInvalidGrantError(f"OAuth token rejection: {err_type} - {err_desc}")
                return MyQAuthError(f"OAuth error: {err_type} - {err_desc}")

            # Format C or B (Flat)
            if "code" in data and ("message" in data or "description" in data):
                code = str(data.get("code", ""))
                message = str(data.get("message", ""))
                description = str(data.get("description", ""))

            # Format A (Nested)
            elif "errors" in data and isinstance(data["errors"], dict):
                err_obj = data["errors"]
                code = str(err_obj.get("code", ""))
                message = str(err_obj.get("message", ""))
                description = str(err_obj.get("description", ""))

        summary = description or message or text or f"HTTP {status_code}"

        # Error code mapping according to PROTOCOL.md §9.2
        if code == "401.102" or "invalid_grant" in summary.lower():
            return MyQInvalidGrantError(f"OAuth invalid grant ({code}): {summary}")

        if status_code == 401 or code == "401.209":
            return MyQAuthError(f"Unauthorized ({code or status_code}): {summary}")

        if status_code == 429 or code == "309":
            return MyQRateLimitError(f"Rate limited by myQ cloud ({code or status_code}): {summary}")

        if code in ("138", "-9990") or status_code == 502:
            return MyQOfflineError(f"Device or cloud gateway offline ({code or status_code}): {summary}")

        if code == "310":
            return MyQCommandError(f"Safety obstruction detected (code 310): {summary}")

        if code == "321":
            return MyQCommandError(f"Transition state conflict (code 321): {summary}")

        if status_code == 404 or code in ("127", "404"):
            return MyQDeviceNotFoundError(f"Device or endpoint not found ({code or status_code}): {summary}")

        return MyQError(f"myQ API request failed [{code or status_code}]: {summary}")

    async def _request(
        self,
        method: str,
        path_or_url: str,
        headers: Optional[dict[str, str]] = None,
        params: Optional[dict[str, Any]] = None,
        data: Optional[Any] = None,
        json: Optional[Any] = None,
        timeout: float = COMMAND_TIMEOUT,
        retry_auth: bool = True,
    ) -> Any:
        """Execute an authenticated API request with proactive and reactive refresh."""
        # Proactive refresh: refresh 5 minutes before expiration (PROTOCOL.md §3.7)
        if self.refresh_token and self.is_token_expiring(leeway=TOKEN_EXPIRATION_LEEWAY):
            _LOGGER.debug(
                "Proactive token refresh triggered (token expiring within %ss)",
                TOKEN_EXPIRATION_LEEWAY,
            )
            await self.refresh_tokens()

        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        else:
            url = f"{API_BASE_URL}{path_or_url}"

        req_headers = self._get_api_headers()
        if headers:
            req_headers.update(headers)

        status_code, parsed_json, text, _ = await self._send_http_request(
            method=method,
            url=url,
            headers=req_headers,
            params=params,
            data=data,
            json=json,
            timeout=timeout,
        )

        # Successful status
        if 200 <= status_code < 300:
            if status_code == 204 or not text:
                return {}
            return parsed_json if parsed_json is not None else {}

        # Reactive refresh: on 401, perform single reactive refresh and replay request
        if status_code == 401 and retry_auth and self.refresh_token:
            _LOGGER.warning("HTTP 401 received from %s; performing single reactive token refresh", url)
            try:
                await self.refresh_tokens(force=True)
            except MyQInvalidGrantError:
                raise
            except Exception as exc:
                raise MyQAuthError(f"Reactive token refresh failed: {exc}") from exc

            return await self._request(
                method=method,
                path_or_url=path_or_url,
                headers=headers,
                params=params,
                data=data,
                json=json,
                timeout=timeout,
                retry_auth=False,
            )

        # Transition state conflict (code 321) e.g., door already closed/open
        if status_code == 409 and isinstance(parsed_json, dict):
            err_code = str(
                parsed_json.get("code")
                or (
                    parsed_json.get("errors", {})
                    if isinstance(parsed_json.get("errors"), dict)
                    else {}
                ).get("code", "")
            )
            if err_code == "321":
                _LOGGER.info("Device already in requested transition state (code 321)")
                return {"code": 321, "status": "already_in_state"}

        raise self._parse_error(status_code, parsed_json, text)

    # -------------------------------------------------------------------------
    # Account & Device Discovery (PROTOCOL.md §5 & §6)
    # -------------------------------------------------------------------------
    async def get_accounts(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Retrieve accounts associated with the user credentials (cached)."""
        if self._accounts_cache is not None and not force_refresh:
            return self._accounts_cache

        path = f"/api/{API_VERSION}/accounts"
        data = await self._request("GET", path)
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
        self._accounts_cache = accounts
        if accounts and not self.account_id:
            self.account_id = accounts[0].get("id")
        return accounts

    async def get_account_id(self) -> str:
        """Return authoritative account ID, querying account discovery if not cached."""
        if self.account_id:
            return self.account_id
        accounts = await self.get_accounts()
        if not accounts or "id" not in accounts[0]:
            raise MyQError("No valid account ID found for authenticated user")
        self.account_id = accounts[0]["id"]
        return self.account_id

    async def get_devices(self, account_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve all devices for the account (PROTOCOL.md §6.1)."""
        acc_id = account_id or await self.get_account_id()
        path = f"/api/{API_VERSION}/Accounts/{acc_id}/Devices"
        data = await self._request("GET", path)
        if isinstance(data, dict):
            items = data.get("items", [])
        elif isinstance(data, list):
            items = data
        else:
            items = []

        self._devices_cache = {
            dev.get("serial_number"): dev
            for dev in items
            if isinstance(dev, dict) and dev.get("serial_number")
        }
        return items

    async def get_device(self, serial_number: str, account_id: Optional[str] = None) -> dict[str, Any]:
        """Retrieve a specific device by its serial number."""
        devices = await self.get_devices(account_id=account_id)
        for dev in devices:
            if dev.get("serial_number") == serial_number:
                return dev
        raise MyQDeviceNotFoundError(f"Device with serial number {serial_number} not found")

    # -------------------------------------------------------------------------
    # Command Actuation Endpoints (PROTOCOL.md §8)
    # -------------------------------------------------------------------------
    async def open_door(self, serial_number: str, account_id: Optional[str] = None) -> bool:
        """Send command to open garage door (empty PUT body, timeout non-retry)."""
        acc_id = account_id or await self.get_account_id()
        path = f"/api/{API_VERSION}/accounts/{acc_id}/door_openers/{serial_number}/open"
        headers = {"Content-Type": "application/json; charset=UTF-8"}
        await self._request("PUT", path, headers=headers, data=b"", timeout=COMMAND_TIMEOUT)
        return True

    async def close_door(self, serial_number: str, account_id: Optional[str] = None) -> bool:
        """Send command to close garage door (empty PUT body, timeout non-retry)."""
        acc_id = account_id or await self.get_account_id()
        path = f"/api/{API_VERSION}/accounts/{acc_id}/door_openers/{serial_number}/close"
        headers = {"Content-Type": "application/json; charset=UTF-8"}
        await self._request("PUT", path, headers=headers, data=b"", timeout=COMMAND_TIMEOUT)
        return True

    async def set_door_state(
        self, device_id: str, action: str, account_id: Optional[str] = None
    ) -> bool:
        """Set garage door state: 'open' or 'close'."""
        act = action.strip().lower()
        if act in ("open", "opening", STATE_OPEN):
            return await self.open_door(device_id, account_id=account_id)
        if act in ("close", "closing", STATE_CLOSED):
            return await self.close_door(device_id, account_id=account_id)
        raise ValueError(f"Invalid door action '{action}'; expected 'open' or 'close'")

    async def set_light_state(
        self, serial_number: str, state: bool, account_id: Optional[str] = None
    ) -> bool:
        """Turn lamp/light ON or OFF (empty PUT body, timeout non-retry)."""
        acc_id = account_id or await self.get_account_id()
        action = "on" if state else "off"
        path = f"/api/{API_VERSION}/accounts/{acc_id}/lamps/{serial_number}/{action}"
        headers = {"Content-Type": "application/json; charset=UTF-8"}
        await self._request("PUT", path, headers=headers, data=b"", timeout=COMMAND_TIMEOUT)
        return True

    # -------------------------------------------------------------------------
    # Device Classification & Hierarchy Helpers (PROTOCOL.md §6.2 & §6.3)
    # -------------------------------------------------------------------------
    @staticmethod
    def is_garage_door(device: dict[str, Any]) -> bool:
        """Return True if device is a garage door opener or operator."""
        family = str(device.get("device_family", "")).lower()
        dev_type = str(device.get("device_type", "")).lower()
        return (
            family == DEVICE_FAMILY_GARAGE_DOOR
            or "garagedoor" in dev_type
            or "dooropener" in dev_type
            or "overheadopener" in dev_type
        )

    @staticmethod
    def is_lamp(device: dict[str, Any]) -> bool:
        """Return True if device is a lamp module or light switch."""
        family = str(device.get("device_family", "")).lower()
        dev_type = str(device.get("device_type", "")).lower()
        return family == DEVICE_FAMILY_LAMP or "lamp" in dev_type or "light" in dev_type

    @staticmethod
    def is_gateway(device: dict[str, Any]) -> bool:
        """Return True if device is a hub or gateway."""
        family = str(device.get("device_family", "")).lower()
        dev_type = str(device.get("device_type", "")).lower()
        return family == DEVICE_FAMILY_GATEWAY or "gateway" in dev_type or "hub" in dev_type

    @staticmethod
    def get_parent_serial_number(device: dict[str, Any]) -> Optional[str]:
        """Resolve parent gateway serial number according to PROTOCOL.md §6.2."""
        state = device.get("state", {})
        if isinstance(state, dict):
            if "parent_serial_number" in state and state["parent_serial_number"]:
                return str(state["parent_serial_number"])
            if "gatewayID" in state and state["gatewayID"]:
                return str(state["gatewayID"])
            if "gatewayId" in state and state["gatewayId"]:
                return str(state["gatewayId"])
        return None

    @staticmethod
    def get_door_state(device: dict[str, Any]) -> str:
        """Extract current door_state string."""
        state = device.get("state", {})
        if isinstance(state, dict):
            return str(state.get("door_state", STATE_UNKNOWN)).lower()
        return STATE_UNKNOWN

    @staticmethod
    def is_device_online(device: dict[str, Any]) -> bool:
        """Extract online reachability status."""
        state = device.get("state", {})
        if isinstance(state, dict):
            return bool(state.get("online", True))
        return True

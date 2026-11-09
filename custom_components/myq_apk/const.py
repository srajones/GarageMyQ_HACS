"""Constants and custom exceptions for the Chamberlain/LiftMaster myQ integration.

Extracted offline from Android APK com.chamberlain.android.liftmaster.myq (v5.323.0.1132341)
and conforming to docs/PROTOCOL.md and Requirement R3 anti-lockout architecture.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final[str] = "myq_apk"

# -----------------------------------------------------------------------------
# Base URLs & Endpoints (PROTOCOL.md §2.1 & §3.1)
# -----------------------------------------------------------------------------
AUTH_BASE_URL: Final[str] = "https://partner-identity.myq-cloud.com"
AUTHORIZE_URL: Final[str] = f"{AUTH_BASE_URL}/connect/authorize"
TOKEN_URL: Final[str] = f"{AUTH_BASE_URL}/connect/token"
REVOCATION_URL: Final[str] = f"{AUTH_BASE_URL}/connect/revocation"

API_BASE_URL: Final[str] = "https://api.myq-cloud.com"
API_BASE_URL_FALLBACKS: Final[list[str]] = [
    "https://api-use2.myq-cloud.com",
    "https://api-usw2.myq-cloud.com",
    "https://api-west.myq-cloud.com",
]

# -----------------------------------------------------------------------------
# OAuth2 & PKCE Cryptographic Specifications (PROTOCOL.md §3.1 & §3.2)
# -----------------------------------------------------------------------------
CLIENT_ID: Final[str] = "ANDROID_CGI_MYQ"
REDIRECT_URI: Final[str] = "com.myqops://android"
DEFAULT_SCOPE: Final[str] = "MyQ_Residential offline_access"
SECONDARY_SCOPES: Final[str] = "myq:login myq:delivery offline_access"
RESPONSE_TYPE: Final[str] = "code"
CODE_CHALLENGE_METHOD: Final[str] = "S256"
PROMPT: Final[str] = "login"
UI_LOCALES: Final[str] = "en"
ACR_VALUES: Final[str] = "unifiedFlow brand:liftmaster"

# -----------------------------------------------------------------------------
# Exact Mobile Client Headers & Fingerprints (PROTOCOL.md §4.1)
# -----------------------------------------------------------------------------
BRAND_ID: Final[str] = "1"  # 1 for LiftMaster flavor, 2 for Chamberlain
APP_VERSION: Final[str] = "5.323.0.1132341"
API_VERSION: Final[str] = "v6.0"
API_VERSION_LEGACY: Final[str] = "v5.1"
CULTURE: Final[str] = "en"

USER_AGENT_API: Final[str] = "Pixel 7/Android 14"
USER_AGENT_WEB: Final[str] = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/UQ1A.240105.004) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36"
)

HEADER_BRAND_ID: Final[str] = "BrandId"
HEADER_APP_VERSION: Final[str] = "App-Version"
HEADER_API_VERSION: Final[str] = "Api-Version"
HEADER_CULTURE: Final[str] = "Culture"
HEADER_IS_REFRESH: Final[str] = "IsRefresh"
HEADER_APPLICATION_ID: Final[str] = "MyQ-Application-Id"
HEADER_ANTI_CSRF: Final[str] = "AntiCSRF"

# -----------------------------------------------------------------------------
# Garage Door State Strings (PROTOCOL.md §7.1)
# -----------------------------------------------------------------------------
STATE_OPEN: Final[str] = "open"
STATE_CLOSED: Final[str] = "closed"
STATE_OPENING: Final[str] = "opening"
STATE_CLOSING: Final[str] = "closing"
STATE_STOPPED: Final[str] = "stopped"
STATE_AUTOREVERSE: Final[str] = "autoreverse"
STATE_MOVING: Final[str] = "moving"
STATE_UNKNOWN: Final[str] = "unknown"

INTERMEDIATE_STATES: Final[set[str]] = {
    STATE_OPENING,
    STATE_CLOSING,
    STATE_MOVING,
}

STATIONARY_STATES: Final[set[str]] = {
    STATE_OPEN,
    STATE_CLOSED,
    STATE_STOPPED,
    STATE_AUTOREVERSE,
}

# -----------------------------------------------------------------------------
# Lamp / Light State Strings (PROTOCOL.md §7.2)
# -----------------------------------------------------------------------------
LAMP_STATE_ON: Final[str] = "On"
LAMP_STATE_OFF: Final[str] = "Off"

# -----------------------------------------------------------------------------
# Polling Cadence & Timing Rules (PROTOCOL.md §10 & Requirement R3.5)
# -----------------------------------------------------------------------------
DEFAULT_SCAN_INTERVAL: Final[int] = 180  # Default 180s
DEFAULT_SCAN_JITTER: Final[int] = 20  # ±20s random jitter
MIN_SCAN_INTERVAL: Final[int] = 60  # Minimum polling 60s
MAX_SCAN_INTERVAL: Final[int] = 600  # Maximum polling 600s
FAST_POLL_INTERVAL: Final[int] = 5  # Fast burst interval post-command (5s)
FAST_POLL_BURST_MAX: Final[int] = 35  # Max duration of fast burst (35s)
TARGET_STATE_TIMEOUT: Final[int] = 45  # Safety timeout to reach target state (45s)
COMMAND_TIMEOUT: Final[float] = 20.0  # HTTP request timeout (20s)
TOKEN_EXPIRATION_LEEWAY: Final[int] = 300  # Proactive refresh window (5 min = 300s)
DEFAULT_TOKEN_LIFESPAN: Final[int] = 3600  # Default OAuth2 access token lifespan (1h)

# -----------------------------------------------------------------------------
# Device Families & Types (PROTOCOL.md §6.3)
# -----------------------------------------------------------------------------
DEVICE_FAMILY_GATEWAY: Final[str] = "gateway"
DEVICE_FAMILY_GARAGE_DOOR: Final[str] = "garagedoor"
DEVICE_FAMILY_LAMP: Final[str] = "lamp"
DEVICE_FAMILY_LOCKS: Final[str] = "locks"
DEVICE_FAMILY_CAMERA: Final[str] = "camera"

DEVICE_TYPE_HUB: Final[str] = "hub"
DEVICE_TYPE_GATEWAY: Final[str] = "gateway"
DEVICE_TYPE_GARAGE_DOOR_OPENER: Final[str] = "garagedooropener"
DEVICE_TYPE_WIFI_GARAGE_DOOR_OPENER: Final[str] = "wi-figaragedooropener"
DEVICE_TYPE_WIFI_OVERHEAD_OPENER: Final[str] = "wi-fioverheadopener"
DEVICE_TYPE_VIRTUAL_GARAGE_DOOR_OPENER: Final[str] = "virtualgaragedooropener"
DEVICE_TYPE_LAMP_MODULE: Final[str] = "lampmodule"
DEVICE_TYPE_WALL_SWITCH: Final[str] = "wallswitch"

# -----------------------------------------------------------------------------
# Configuration Flow & Entry Storage Keys
# -----------------------------------------------------------------------------
CONF_ACCESS_TOKEN: Final[str] = "access_token"
CONF_REFRESH_TOKEN: Final[str] = "refresh_token"
CONF_EXPIRES_AT: Final[str] = "expires_at"
CONF_ACCOUNT_ID: Final[str] = "account_id"
CONF_AUTH_MODE: Final[str] = "auth_mode"
CONF_SCAN_INTERVAL: Final[str] = "scan_interval"
CONF_REDIRECT_URL: Final[str] = "redirect_url"
CONF_CODE_VERIFIER: Final[str] = "code_verifier"

AUTH_MODE_AUTOMATED: Final[str] = "automated"  # Mode A (PKCE + credentials)
AUTH_MODE_MANUAL: Final[str] = "manual"  # Mode B (browser redirect URL paste)


# -----------------------------------------------------------------------------
# Custom Exception Hierarchy
# -----------------------------------------------------------------------------
class MyQError(Exception):
    """Base exception for all myQ integration errors."""


class MyQAuthError(MyQError):
    """Authentication or authorization failure."""


class MyQInvalidGrantError(MyQAuthError):
    """Fatal OAuth invalid grant rejection.

    Triggered when refresh token is rejected (e.g., HTTP 400 invalid_grant or 401.102).
    Enforces the zero-lockout circuit breaker: NEVER retry username/password in background.
    """


class MyQRateLimitError(MyQError):
    """HTTP 429 Too Many Requests or backend rate limit backoff triggered."""


class MyQOfflineError(MyQError):
    """Device or cloud gateway communication failure / offline state."""


class MyQCommandError(MyQError):
    """Device command rejected by cloud gateway (e.g., safety beam obstruction)."""


class MyQTimeoutError(MyQError):
    """Command timed out. Enforces physical safety invariant: do not retry command payload."""


class MyQDeviceNotFoundError(MyQError):
    """Device or account ID not found."""

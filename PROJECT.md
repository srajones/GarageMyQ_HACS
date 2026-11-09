# Project: GarageMyQ_HACS (Chamberlain/LiftMaster myQ HACS Integration)

## Architecture
- **Protocol Layer**: Reverse engineered from Android APK `myQ-Garage-Access-Control_5.323.0.1132341_apkcube.apks` using `rea/` decompilation tools. Fully documented in `docs/PROTOCOL.md`.
- **API & Auth Client (`custom_components/myq_apk/api.py`)**:
  - `curl_cffi` TLS client impersonating Android mobile client.
  - OAuth2 + PKCE authorization engine supporting Mode A (automated credentials) and Mode B (manual redirect URL).
  - Single-flight `asyncio.Lock` guarded rolling token refresh with atomic persistence to `ConfigEntry.data`.
  - Circuit-breaker safety: immediate `ConfigEntryAuthFailed` on permanent invalid grant, zero background password login retries.
  - Zero live network traffic to `myq-cloud.com` during testing.
- **Home Assistant Custom Integration (`custom_components/myq_apk/`)**:
  - `DataUpdateCoordinator` managing human-like polling (180s ± 20s jitter, options flow 60-600s), fast-refresh burst (5s for up to 35s), and exponential backoff.
  - Entity Platforms: `cover`, `light`, `binary_sensor`, `sensor`, `button`, `diagnostics`.
  - Unified `DeviceInfo` per physical garage door opener keyed on `(DOMAIN, serial_number)`.
  - Physical safety invariants: command idempotency, timeout non-retry, stall/autoreverse detection.
- **HACS Distribution**:
  - `hacs.json` compliant metadata file at repository root.
  - `manifest.json` with domain `myq_apk`.
- **Adversarial Test Suite (`tests/`)**:
  - 100% mocked network calls using `pytest` and `pytest-asyncio`.
  - Comprehensive race condition, circuit breaker, safety invariant, and hostile payload test coverage.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Git & GitHub Setup | Git init, .gitignore for large files, GitHub private repo creation | M0 | ORIGINAL_REQUEST |
| 2 | APK Deconstruction & Spec Mining | Decompile APK with `rea/`, extract OAuth2/PKCE, headers, discovery, state schemas, command payloads | M1 | ORIGINAL_REQUEST §R2 |
| 3 | Protocol Documentation | Write complete `docs/PROTOCOL.md` specification | M1 | ORIGINAL_REQUEST §R2 |
| 4 | Anti-Lockout Core Client | `api.py` with `curl_cffi`, exact APK headers, single-flight lock, rolling token refresh | M2 | ORIGINAL_REQUEST §R3 |
| 5 | Dual Login Mode Auth | Mode A automated login and Mode B manual redirect URL parsing in `config_flow.py` and `api.py` | M2, M3 | ORIGINAL_REQUEST §R3.2 |
| 6 | Rolling Token Persistence | Immediate atomic update to `ConfigEntry.data` on token refresh | M2, M3 | ORIGINAL_REQUEST §R3.3 |
| 7 | Zero-Lockout Circuit Breaker | Immediate `ConfigEntryAuthFailed` on invalid_grant, no background password retries | M2 | ORIGINAL_REQUEST §R3.1 |
| 8 | Human-like Polling & Fast Burst | 180s ± 20s jitter polling, 5s burst post-command in `coordinator.py` | M3 | ORIGINAL_REQUEST §R3.5 |
| 9 | Command Idempotency | No-op on duplicate open/close calls | M3 | ORIGINAL_REQUEST §R4.1 |
| 10 | Timeout Non-Retry | Trigger coordinator refresh instead of resending ambiguous timed-out commands | M3 | ORIGINAL_REQUEST §R4.2 |
| 11 | Reversal & Stall Detection | Binary sensor problem flag on autoreverse, stopped, or 45s target timeout | M3 | ORIGINAL_REQUEST §R4.3 |
| 12 | Garage Door Cover Entity | `CoverDeviceClass.GARAGE` with open, close, and state reporting | M3 | ORIGINAL_REQUEST §R5 |
| 13 | Opener Work Light Entity | Light entity toggling opener light for capable devices | M3 | ORIGINAL_REQUEST §R5 |
| 14 | Problem & Connectivity Binary Sensors | Problem (`PROBLEM`) and cloud reachability (`CONNECTIVITY`) | M3 | ORIGINAL_REQUEST §R5 |
| 15 | Diagnostic Sensors & Button | Door status, last event, RSSI, battery, session health sensors, and refresh button | M3 | ORIGINAL_REQUEST §R5 |
| 16 | Redacted Diagnostics Platform | Redact tokens, passwords, usernames, account IDs in diagnostics | M3 | ORIGINAL_REQUEST §R5 |
| 17 | HACS Compliance & Manifest | Valid `hacs.json` and `manifest.json` packaging | M4 | ORIGINAL_REQUEST §R5 |
| 18 | Adversarial Test Suite | Comprehensive pytest suite covering race conditions, safety, circuit breakers, and hostile payloads | M5 | ORIGINAL_REQUEST §R6 |
| 19 | Integrity Forensic Audit | Forensic verification of genuine implementation with zero hardcoding/facades | M6 | ORIGINAL_REQUEST Acceptance Criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | Git & GitHub Setup | Git init, .gitignore, create private GitHub repository `GarageMyQ_HACS` | none | DONE |
| M1 | APK Protocol Deconstruction | Unpack APK via `rea/`, extract protocol details, write `docs/PROTOCOL.md` | M0 | DONE |
| M2 | Core Auth & API Client | Implement `const.py` and `api.py` with anti-lockout safeguards | M1 | DONE |
| M3 | Home Assistant Integration | Implement `__init__.py`, `config_flow.py`, `coordinator.py`, `cover.py`, `light.py`, `binary_sensor.py`, `sensor.py`, `button.py`, `diagnostics.py`, translations | M2 | DONE |
| M4 | HACS Packaging | Implement `hacs.json`, validate `manifest.json` and packaging structure | M3 | DONE |
| M5 | Adversarial Test Suite | Implement comprehensive tests in `tests/` with 100% mocked network calls | M3 | DONE |
| M6 | Forensic Audit & Final Delivery | Forensic audit verification, clean gate checks, and GitHub push | M4, M5 | PLANNED |

## Interface Contracts
### API Client (`MyQClient`) ↔ Coordinator (`MyQDataUpdateCoordinator`)
- `async def authenticate(username: str, password: str) -> dict`: Returns token payload `{access_token, refresh_token, expires_in, ...}`.
- `async def authenticate_with_redirect_url(redirect_url: str) -> dict`: Mode B manual code exchange.
- `async def refresh_tokens() -> dict`: Single-flight guarded refresh returning new rolling tokens.
- `async def get_devices() -> list[dict]`: Fetches accounts and all garage door / hub / light device schemas.
- `async def set_door_state(device_id: str, action: str) -> bool`: Issues open/close command.
- `async def set_light_state(device_id: str, state: bool) -> bool`: Toggles light.

### Coordinator ↔ Entity Platforms
- Coordinator stores structured device state dictionary keyed by `serial_number`.
- Each entity subscribes to coordinator updates via `CoordinatorEntity`.
- Native `DeviceInfo` constructor:
  `DeviceInfo(identifiers={(DOMAIN, serial_number)}, name=device_name, manufacturer="Chamberlain", model=model_name, sw_version=firmware_version)`

## Code Layout
```
/Users/trilben/GarageMyQ_HACS/
├── .gitignore
├── hacs.json
├── docs/
│   └── PROTOCOL.md
├── custom_components/
│   └── myq_apk/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── api.py
│       ├── coordinator.py
│       ├── config_flow.py
│       ├── cover.py
│       ├── light.py
│       ├── binary_sensor.py
│       ├── sensor.py
│       ├── button.py
│       ├── diagnostics.py
│       ├── strings.json
│       └── translations/
│           └── en.json
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_api_auth.py
    ├── test_config_flow.py
    ├── test_coordinator.py
    ├── test_safety_invariants.py
    ├── test_entities.py
    └── test_hostile_payloads.py
```

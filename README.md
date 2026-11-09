# Chamberlain / LiftMaster myQ (APK) — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![homeassistant](https://img.shields.io/badge/Home%20Assistant-2024.1.0+-blue.svg?style=for-the-badge)](https://www.home-assistant.io/)
[![manifest_version](https://img.shields.io/badge/Version-1.0.0-green.svg?style=for-the-badge)](custom_components/myq_apk/manifest.json)
[![license](https://img.shields.io/badge/License-MIT-orange.svg?style=for-the-badge)](LICENSE)

A production-grade, self-healing Home Assistant custom integration for **Chamberlain and LiftMaster myQ** garage door openers and accessories. 

Built directly from reverse-engineering the official Android APK (`v5.323.0.1132341`), this integration bypasses web-scraper deprecation and Cloudflare WAF blocking by faithfully impersonating the official mobile app's OAuth2 + PKCE authentication flow, TLS fingerprint, and API protocol.

---

## Table of Contents
1. [Why This Integration?](#why-this-integration)
2. [Key Features](#key-features)
3. [Anti-Lockout & Self-Healing Architecture](#anti-lockout--self-healing-architecture)
4. [Physical Garage Door Safety Invariants](#physical-garage-door-safety-invariants)
5. [Supported Entities & Devices](#supported-entities--devices)
6. [Installation](#installation)
   - [Method 1: HACS (Recommended)](#method-1-hacs-recommended)
   - [Method 2: Manual Installation](#method-2-manual-installation)
7. [Configuration](#configuration)
   - [Mode A: Automated Login](#mode-a-automated-login)
   - [Mode B: Manual Browser Login (Fallback)](#mode-b-manual-browser-login-fallback)
   - [Options & Polling Customization](#options--polling-customization)
8. [Diagnostics & Privacy](#diagnostics--privacy)
9. [Troubleshooting & FAQ](#troubleshooting--faq)
10. [Technical Protocol Reference](#technical-protocol-reference)
11. [Disclaimer](#disclaimer)

---

## Why This Integration?

In late 2023, Chamberlain restricted their public web API and deployed aggressive Cloudflare Web Application Firewall (WAF) mitigations that permanently broke traditional web-scraping integrations.

Rather than scraping undocumented web endpoints, **GarageMyQ_HACS** implements the exact protocol used by Chamberlain's official Android mobile application:
- **Direct OAuth2 + PKCE**: Modern token exchange matching Android client parameters (`ANDROID_CGI_MYQ`, `S256` PKCE challenge).
- **TLS Fingerprint Impersonation**: Uses `curl_cffi` to mimic official Android 14 / Pixel 7 TLS signatures.
- **Mobile Application Headers**: Transmits exact header sets (`BrandId`, `App-Version`, `Api-Version: v6.0`, `Culture: en`, `MyQ-Application-Id`, `AntiCSRF`).
- **Single-Flight Rolling Refresh Tokens**: Concurrency-locked token rotation with zero background password looping, preventing account lockouts.

---

## Key Features

- **Full Garage Door Control (`cover`)**: Open, close, and accurately report state (`open`, `closed`, `opening`, `closing`, `stopped`, `autoreverse`).
- **Opener Work Light (`light`)**: Turn on/off the built-in overhead opener work light or smart lamp modules.
- **Problem & Obstruction Detection (`binary_sensor`)**: Real-time diagnostic alerts for beam obstructions, 45s movement stalls, mid-travel stops, autoreverse events, and sensor low battery.
- **Cloud Connectivity Monitoring (`binary_sensor`)**: Continuous tracking of opener gateway connectivity and cloud reachability.
- **Telemetry Sensors (`sensor`)**: Door status, last movement event timestamp, Wi-Fi RSSI signal strength, backup battery percentage, and session health.
- **Manual Force Refresh (`button`)**: On-demand diagnostic button to refresh device telemetry immediately.
- **Dual Authentication Modes**: Automated username/password login or manual browser redirect URL paste for seamless MFA/CAPTCHA handling.
- **Human-Like Polling Cadence**: 180s ± 20s randomized jitter polling with post-command fast-refresh bursts.

---

## Anti-Lockout & Self-Healing Architecture

To permanently prevent account lockouts, API bans, and credential exhaustion, this integration enforces six fundamental architectural safeguards:

| # | Safeguard | Description |
|---|---|---|
| **1** | **Zero Background Password Retries** | The background coordinator **never** stores or re-attempts password logins. If a rolling refresh token is permanently rejected (`invalid_grant`), the integration halts background polling and immediately raises `ConfigEntryAuthFailed`, prompting the user to re-authenticate via the HA UI. |
| **2** | **Dual Login Modes in UI** | Supports **Mode A** (automated OAuth2 + PKCE exchange with username/password) and **Mode B** (manual browser OAuth2 flow where you copy/paste the `com.myqops://android?code=...` redirect URL), allowing you to bypass any web CAPTCHAs or MFA challenges. |
| **3** | **Single-Flight Rolling Refresh Tokens** | Token refreshes are synchronized with an `asyncio.Lock` single-flight gate so multiple concurrent requests trigger **exactly one** network refresh. New rolling refresh tokens are atomically persisted to `ConfigEntry.data` immediately. Proactive refresh occurs 5 minutes prior to expiration; reactive refresh runs once on HTTP 401. |
| **4** | **TLS Impersonation & Exact Headers** | Uses `curl_cffi` to match the TLS fingerprint of an Android 14 mobile device. Every API request transmits the exact header fingerprint extracted from the official APK, including persistent client installation UUIDs. |
| **5** | **Human-Like Polling with Jitter & Fast Burst** | Polling defaults to **180s ± 20s random jitter** (configurable between 60s and 600s in Options). When a command (open/close/light) is dispatched, the coordinator temporarily activates a **5-second fast-burst mode** (for up to 35 seconds) to capture door transit without flooding the cloud. |
| **6** | **Zero Live Test Traffic** | All test suites and development fixtures use 100% mocked HTTP responses; live Chamberlain cloud services are never touched during automated verification. |

---

## Physical Garage Door Safety Invariants

Physical garage doors can cause severe damage or injury if sent duplicate or ambiguous commands. This integration strictly enforces three physical safety invariants:

1. **Strict Command Idempotency**:
   - `async_open_cover` sends **0 API requests** if the door is already `open` or `opening`.
   - `async_close_cover` sends **0 API requests** if the door is already `closed` or `closing`.
   - Repeated button clicks or automation loops will never cause unnecessary door cycling.
2. **Ambiguous Timeout Non-Retry Policy**:
   - If an HTTP command times out after transmission, the integration **never** re-sends the command payload. Re-sending an ambiguous command could cause a door to reverse mid-transit or bounce. Instead, it triggers an immediate coordinator status poll to verify true physical state.
3. **Stall & Reversal Detection (45-Second Safety Watchdog)**:
   - When a door command is issued, a 45-second transit timer starts.
   - If the door does not reach its target state within 45 seconds, or if the door reports `autoreverse` or `stopped`, the `binary_sensor.<name>_problem` entity transitions to `on` and logs a descriptive diagnostic error.

---

## Supported Entities & Devices

All entities for a given garage door opener are grouped under a single unified Home Assistant device keyed by serial number:

| Platform | Entity | Description |
|---|---|---|
| `cover` | `Garage Door` | Main garage door opener cover entity (`open`, `close`, state). |
| `light` | `Work Light` | Opener built-in work light or smart lamp module (on/off). |
| `binary_sensor` | `Problem` | Diagnostic sensor (`PROBLEM` class) alerting on stalls, obstructions, autoreverse, or low sensor battery. |
| `binary_sensor` | `Cloud Connectivity` | Connectivity status (`CONNECTIVITY` class) indicating gateway online status. |
| `sensor` | `Door Status` | Precise door operational state string (`open`, `closed`, `opening`, `closing`, `stopped`, `autoreverse`). |
| `sensor` | `Last Event` | ISO timestamp of the last door state transition (`TIMESTAMP` class). |
| `sensor` | `Wi-Fi Signal` | Gateway Wi-Fi RSSI strength in dBm (`SIGNAL_STRENGTH` class). |
| `sensor` | `Battery` | Backup battery percentage or tilt sensor battery (`BATTERY` class). |
| `sensor` | `Session Health` | Diagnostic sensor reporting OAuth token lifetime, expires-in, and auth status. |
| `button` | `Refresh Status Now` | Forces an immediate coordinator refresh of all account devices. |
| `diagnostics` | *Diagnostics Platform* | Downloadable Home Assistant diagnostics with all tokens and PII redacted. |

---

## Installation

### Method 1: HACS (Recommended)

1. Ensure [HACS (Home Assistant Community Store)](https://hacs.xyz/) is installed.
2. In Home Assistant, navigate to **HACS** > **Integrations**.
3. Click the three dots `⋮` in the top right corner and select **Custom repositories**.
4. Enter the repository details:
   - **Repository**: `https://github.com/srajones/GarageMyQ_HACS`
   - **Type**: `Integration`
5. Click **Add**.
6. Search for **Chamberlain / LiftMaster myQ (APK)** in the integration list and click **Download**.
7. Restart Home Assistant when prompted.

### Method 2: Manual Installation

1. Download the latest release from the [Releases](https://github.com/srajones/GarageMyQ_HACS/releases) page.
2. Extract the archive and copy the `custom_components/myq_apk` directory into your Home Assistant configuration directory:
   ```
   config/
   └── custom_components/
       └── myq_apk/
           ├── __init__.py
           ├── manifest.json
           ├── const.py
           ├── api.py
           ├── coordinator.py
           ├── config_flow.py
           ├── cover.py
           ├── light.py
           ├── binary_sensor.py
           ├── sensor.py
           ├── button.py
           ├── diagnostics.py
           ├── strings.json
           └── translations/
   ```
3. Restart Home Assistant.

---

## Configuration

Once installed, add the integration through the Home Assistant UI:

1. Go to **Settings** > **Devices & Services** > **Integrations**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **Chamberlain / LiftMaster myQ (APK)**.
4. Select your preferred authentication mode:

```
┌────────────────────────────────────────┐
│       myQ Authentication Mode          │
├────────────────────────────────────────┤
│  ○ Automated Login (Username/Password) │
│  ○ Manual Login (Browser OAuth URL)    │
└────────────────────────────────────────┘
```

### Mode A: Automated Login

- Choose **Automated Login (Username & Password)**.
- Enter your myQ account email and password.
- The integration will perform the OAuth2 + PKCE challenge automatically, retrieve rolling refresh tokens, and discover your garage doors.

### Mode B: Manual Browser Login (Fallback)

If your account has multi-factor authentication (MFA), SSO, or encounters CAPTCHA challenges during automated login:

1. Choose **Manual Login (Browser OAuth URL Paste)**.
2. Home Assistant will display an official authorization URL:
   ```
   https://partner-identity.myq-cloud.com/connect/authorize?...
   ```
3. Click the link (or copy/paste it into your web browser) and log in to your myQ account.
4. After signing in, your browser will redirect to a page beginning with:
   ```
   com.myqops://android?code=...
   ```
   *(Your browser may show a "Page cannot be reached" or "Open with App" message — this is expected!)*
5. Copy the **full URL** from your browser address bar.
6. Paste the entire URL (or just the `code` parameter) into the Home Assistant dialog and click **Submit**.

### Options & Polling Customization

To configure background polling:
1. In **Settings** > **Devices & Services**, find your **Chamberlain / LiftMaster myQ** entry.
2. Click **Configure**.
3. Adjust the **Scan Interval** (allowed range: `60` to `600` seconds; default: `180` seconds).
4. The integration automatically adds a random ±20s jitter to whichever interval you select to ensure human-like request distribution.

---

## Diagnostics & Privacy

### Sanitized Diagnostics
This integration fully supports native Home Assistant diagnostic exports. Go to your myQ device page and click **Download Diagnostics**.

All sensitive data is automatically redacted before export:
- Passwords and usernames are stripped.
- OAuth `access_token` and `refresh_token` are redacted (`**REDACTED**`).
- Internal Chamberlain `account_id` and device credentials are fully obfuscated.

### Credential Storage
Credentials and rolling tokens are stored securely in Home Assistant's encrypted `.storage` keyrings and are never transmitted to any third-party server besides Chamberlain's official identity and API endpoints.

---

## Troubleshooting & FAQ

### 1. Why does my cover show "Problem" state?
The `binary_sensor.<name>_problem` entity turns `on` if:
- The safety optical beam was obstructed during closing.
- A physical stall occurred (door did not reach target position within 45 seconds).
- The opener reported an `autoreverse` or `stopped` condition.
- A tilt sensor reported low battery.

To clear a problem state, check the physical garage door path and cycle the door using your wall button or the integration.

### 2. What happens if my session expires?
If Chamberlain invalidates your refresh token, Home Assistant will create a persistent notification:
> **"Re-authentication required"**

Click **Reconfigure** on the integration card to re-enter your credentials. The integration never tries bad credentials in an infinite loop.

### 3. Does this integration support multiple garage doors?
Yes! All hubs, garage door openers, and lamp modules linked to your myQ account are automatically discovered and configured with individual entities.

### 4. What is fast-burst polling?
When you send an open, close, or light toggle command from Home Assistant, the integration temporarily switches from its 180s cycle to 5s burst polling (up to 35s). This gives you rapid state feedback in your dashboard while keeping overall daily cloud traffic minimal.

---

## Technical Protocol Reference

For comprehensive protocol reverse-engineering notes, endpoints, cryptographic specifications, header fingerprints, and payload schemas extracted from the Android APK, see:

📖 **[docs/PROTOCOL.md](docs/PROTOCOL.md)**

---

## Disclaimer

This custom integration is an independent open-source project and is **not** affiliated with, endorsed by, or associated with The Chamberlain Group LLC, LiftMaster, or any of their subsidiaries. All product names, trademarks, and registered trademarks are property of their respective owners. This integration is provided strictly for educational and personal home automation use.

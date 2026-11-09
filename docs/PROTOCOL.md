# myQ Android Application Protocol Specification (v6.0)

**Target Artifact**: `myQ-Garage-Access-Control_5.323.0.1132341_apkcube.apks`  
**Application Package**: `com.chamberlain.android.liftmaster.myq`  
**Application Version**: `5.323.0.1132341` (VersionCode: `1132341`)  
**Investigation Mode**: 100% Offline Static Bytecode & DEX Decompilation  
**Zero Live Calls Compliance**: Verified. No live requests were transmitted to `myq-cloud.com`, `myqops.com`, or any remote endpoint.

---

## Table of Contents
1. [Executive Summary & Provenance](#1-executive-summary--provenance)
2. [Base URLs & API Versioning](#2-base-urls--api-versioning)
3. [Authentication Architecture (OAuth2 + PKCE)](#3-authentication-architecture-oauth2--pkce)
   - 3.1 [Core Identity Endpoints](#31-core-identity-endpoints)
   - 3.2 [Cryptographic PKCE S256 Specification](#32-cryptographic-pkce-s256-specification)
   - 3.3 [Authorization Request Flow](#33-authorization-request-flow)
   - 3.4 [Webview Login Selectors & DOM Automation](#34-webview-login-selectors--dom-automation)
   - 3.5 [Token Exchange (`POST /connect/token`)](#35-token-exchange-post-connecttoken)
   - 3.6 [Token Refresh Flow (`IsRefresh: true`)](#36-token-refresh-flow-isrefresh-true)
   - 3.7 [Token Lifespan, Expiration & Circuit Breakers](#37-token-lifespan-expiration--circuit-breakers)
4. [Network Fingerprint & Request Headers](#4-network-fingerprint--request-headers)
   - 4.1 [Global Request Headers Table](#41-global-request-headers-table)
   - 4.2 [Transport & TLS Configuration](#42-transport--tls-configuration)
5. [Account & Household Discovery](#5-account--household-discovery)
   - 5.1 [List Accounts (`GET /api/v6.0/accounts`)](#51-list-accounts-get-apiv60accounts)
   - 5.2 [Account Access & RBAC Permissions (`GET /api/v6.0/accountAccess`)](#52-account-access--rbac-permissions-get-apiv60accountaccess)
6. [Device Discovery & Topology](#6-device-discovery--topology)
   - 6.1 [List Account Devices (`GET /api/v6.0/Accounts/{accountId}/Devices`)](#61-list-account-devices-get-apiv60accountsaccountiddevices)
   - 6.2 [Device Hierarchy & Linkage Architecture](#62-device-hierarchy--linkage-architecture)
   - 6.3 [Device Families & Types Directory](#63-device-families--types-directory)
7. [Device State Schemas](#7-device-state-schemas)
   - 7.1 [Garage Door State (`door_state`)](#71-garage-door-state-door_state)
   - 7.2 [Lamp / Light State (`lamp_state`)](#72-lamp--light-state-lamp_state)
   - 7.3 [Door Sensor State (`door_sensor_state`)](#73-door-sensor-state-door_sensor_state)
   - 7.4 [Telemetry, Battery & Diagnostic Attributes](#74-telemetry-battery--diagnostic-attributes)
   - 7.5 [Active Fault Codes Schema](#75-active-fault-codes-schema)
8. [Command Endpoints, Actions & Payloads](#8-command-endpoints-actions--payloads)
   - 8.1 [Garage Door Open (`PUT .../open`)](#81-garage-door-open-put-open)
   - 8.2 [Garage Door Close (`PUT .../close`)](#82-garage-door-close-put-close)
   - 8.3 [Lamp ON (`PUT .../lamps/{serialNumber}/on`)](#83-lamp-on-put-lampsserialnumberon)
   - 8.4 [Lamp OFF (`PUT .../lamps/{serialNumber}/off`)](#84-lamp-off-put-lampsserialnumberoff)
   - 8.5 [Opener Worklight Settings](#85-opener-worklight-settings)
   - 8.6 [Opener Light Auto-Off Timer](#86-opener-light-auto-off-timer)
   - 8.7 [Opener Vacation / Lock Mode](#87-opener-vacation--lock-mode)
   - 8.8 [Opener Time to Close (TTC)](#88-opener-time-to-close-ttc)
   - 8.9 [Smart Lock Actuation & Auto-Relock](#89-smart-lock-actuation--auto-relock)
   - 8.10 [Device Rename](#810-device-rename)
   - 8.11 [Legacy Fallback Endpoint (`v5.1`)](#811-legacy-fallback-endpoint-v51)
9. [Error Response Schemas & Status Codes](#9-error-response-schemas--status-codes)
   - 9.1 [Error JSON Response Formats](#91-error-json-response-formats)
   - 9.2 [Error Code Mapping Directory](#92-error-code-mapping-directory)
10. [Safety Invariants & Client Timing Rules](#10-safety-invariants--client-timing-rules)
11. [Decompiled Source & Bytecode Index](#11-decompiled-source--bytecode-index)

---

## 1. Executive Summary & Provenance

This document is the authoritative technical specification of the Chamberlain/LiftMaster myQ Android mobile client protocol. It was deconstructed exclusively through offline reverse engineering of production Dalvik Executable (DEX) bytecode (`classes.dex` through `classes9.dex`), Android XML resources, and bundled configuration assets from release package `com.chamberlain.android.liftmaster.myq` version `5.323.0.1132341`.

The architecture consists of:
1. **OAuth2 + PKCE Authorization**: An IdentityServer-based OAuth2 workflow requiring SHA-256 PKCE challenges, returning rolling bearer access tokens and refresh tokens.
2. **RESTful v6.0 API Gateway**: A microservices-backed API hosted on `api.myq-cloud.com` featuring path-versioned resources (`/api/v6.0/...`).
3. **Hierarchy-Linked Device Model**: A tree topology where physical RF and Wi-Fi actuators (garage doors, commercial operators, gate mechanisms, smart lamps) are linked to parent Wi-Fi gateways via serial numbers (`parent_serial_number` / `gatewayID`).
4. **Idempotent Actuation Endpoints**: RESTful `PUT` endpoints with empty bodies for door opening, door closing, and light power state switching.

---

## 2. Base URLs & API Versioning

### 2.1 Authoritative Hostnames
Discovered in `assets/myq-config.json` (lines 50-75) and `classes6.dex` (`ps0/e.java`):

| Service Function | Host URL | Description |
|---|---|---|
| **Identity / OAuth2 Host** | `https://partner-identity.myq-cloud.com` | Production OAuth2 authorization, token exchange, and refresh |
| **Primary API Gateway** | `https://api.myq-cloud.com` | Production REST API gateway for accounts, discovery, and commands |
| **Regional API (US-East-2)** | `https://api-use2.myq-cloud.com` | Regional failover/fail-back gateway |
| **Regional API (US-West-2)** | `https://api-usw2.myq-cloud.com` | Regional failover/fail-back gateway |
| **Regional API (West)** | `https://api-west.myq-cloud.com` | Secondary production gateway |
| **Device Portal Alternate** | `https://api.myqdevice.com` | Alternate device management domain |

### 2.2 API Versioning Routing
Discovered in `classes6.dex` (`ii2/a.java`, `ls0/a.java` `ApiVersion` enum):
- Available API versions in enum: `v1`, `v1.0`, `v1.1`, `v2.0`, `v5`, `v5.1`, `v5.2`, `v5.3`, `v6.0`, `v6.1`, `v6.2`, `v7.0`.
- **Default Production Runtime Version**: **`v6.0`** (instantiated via `ApiVersion.v6_0.getValue()` in service constructors `bz0/y.java` and `uz0/e.java`).
- **Legacy Fallback Version**: **`v5.1`** (used only in older device action endpoints).

---

## 3. Authentication Architecture (OAuth2 + PKCE)

### 3.1 Core Identity Endpoints
Source: `assets/myq-config.json` (lines 50-65) and `classes6.dex` (`po0/k.java`, `po0/s.java`, `OAuthConstants.kt`):

| Parameter | Authoritative Value | Bytecode / Config Source |
|---|---|---|
| **Identity Host** | `https://partner-identity.myq-cloud.com` | `myq-config.json:70` (`connectProduction`) |
| **Authorization Endpoint** | `https://partner-identity.myq-cloud.com/connect/authorize` | `po0/k.java` (`AuthService.kt`) |
| **Token Exchange / Refresh** | `https://partner-identity.myq-cloud.com/connect/token` | `po0/s.java` (`MyQAuthServiceRequests.kt:14,17`) |
| **Token Revocation** | `https://partner-identity.myq-cloud.com/connect/revocation` | `po0/s.java` (`MyQAuthServiceRequests.kt`) |
| **Client ID** | `ANDROID_CGI_MYQ` | `myq-config.json:59` (`myQAppId`) |
| **Default Scope** | `MyQ_Residential offline_access` | `myq-config.json:60` (`myQResidentialScope`) |
| **Secondary / Legacy Scopes** | `myq:login`, `myq:delivery offline_access` | `OAuthConstants.kt` |
| **Redirect URI** | `com.myqops://android` | `myq-config.json:61` (`myQResidentialAuthRedirectUrl`) |
| **Response Type** | `code` | `AuthService.kt` |
| **PKCE Challenge Method** | `S256` | `OAuthConstants.kt`, `po0/j.java:330` |
| **Prompt** | `login` | `OAuthConstants.kt` |
| **UI Locales** | `en` (or device locale) | `AuthService.kt` |
| **ACR Values** | `unifiedFlow brand:liftmaster` | `AuthService.kt` |

### 3.2 Cryptographic PKCE S256 Specification
Decompiled from `classes6.dex` (`po0/j.java` `AuthManagerImpl.kt` lines 330-360):

1. **Code Verifier**:
   - Generated as 32 cryptographically secure random bytes via `java.security.SecureRandom`.
   - Encoded using **Base64 URL-safe, unpadded, no newline** (`flags = 11`, equivalent to `URL_SAFE | NO_PADDING | NO_WRAP`).
   - Generates an unpadded ASCII string of **exactly 43 characters**.
   - Valid character set: `[A-Za-z0-9_-]`.

2. **Code Challenge**:
   - Compute SHA-256 hash of the ASCII byte representation of `code_verifier`:
     $$\text{code\_challenge} = \text{Base64URL\_Unpadded}(\text{SHA-256}(\text{code\_verifier}))$$
   - Method parameter: `code_challenge_method=S256`.

### 3.3 Authorization Request Flow
Constructed by `AuthService.G()`:
```http
GET /connect/authorize?client_id=ANDROID_CGI_MYQ&response_type=code&redirect_uri=com.myqops%3A%2F%2Fandroid&scope=MyQ_Residential%20offline_access&code_challenge=<code_challenge>&code_challenge_method=S256&ui_locales=en&prompt=login&acr_values=unifiedFlow%20brand%3Aliftmaster HTTP/1.1
Host: partner-identity.myq-cloud.com
User-Agent: Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/UQ1A.240105.004) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36
Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8
```

Upon successful user authentication, the identity provider issues an HTTP 302 redirect to:
```
com.myqops://android?code=<authorization_code>&scope=MyQ_Residential%20offline_access&session_state=...
```
Or upon failure:
```
com.myqops://android?errorId=<error_uuid>&error=access_denied
```

### 3.4 Webview Login Selectors & DOM Automation
Decompiled from `classes6.dex` (`po0/j.java` `AuthManagerImpl.kt` lines 380-455):

When rendering the interactive webview login page, the official application executes the following DOM operations:

| Form Element / Selector | Target Selector / Script | Purpose |
|---|---|---|
| **Email Input** | `document.getElementById('Email')` | `document.getElementById('Email').value = window.atob('<base64_username>');` |
| **Password Input** | `document.getElementById('Password')` | `document.getElementById('Password').value = window.atob('<base64_password>');` |
| **Sign-In Button** | `document.getElementById('btn-signin')` | `l=document.getElementById('btn-signin'); l.disabled = false; l.click();` |
| **Validation Error Element** | `document.getElementById('validation-errors')` | Scraped for inline form validation error messages |
| **Account Approval Error** | `document.getElementById('email-not-approved')` | Scraped for pending email verification / approval errors |
| **Redirect Interception** | URL scheme `com.myqops://android` | Intercepted in `AuthWebViewClient.shouldOverrideUrlLoading` |

### 3.5 Token Exchange (`POST /connect/token`)
Decompiled from `classes6.dex` (`po0/s.java` `MyQAuthServiceRequests.kt:14`):

- **Endpoint**: `POST https://partner-identity.myq-cloud.com/connect/token`
- **Request Headers**:
  ```http
  Content-Type: application/x-www-form-urlencoded
  BrandId: 1
  App-Version: 5.323.0.1132341
  User-Agent: Pixel 7/Android 14
  ```
- **Form-Urlencoded Payload**:
  ```
  client_id=ANDROID_CGI_MYQ
  &grant_type=authorization_code
  &code=<authorization_code>
  &redirect_uri=com.myqops%3A%2F%2Fandroid
  &code_verifier=<code_verifier>
  &scope=MyQ_Residential%20offline_access
  ```
- **Success Response (200 OK JSON)**:
  ```json
  {
    "access_token": "ey...",
    "refresh_token": "7A...",
    "expires_in": 3600,
    "token_type": "Bearer",
    "scope": "MyQ_Residential offline_access"
  }
  ```

### 3.6 Token Refresh Flow (`IsRefresh: true`)
Decompiled from `classes6.dex` (`po0/s.java` `MyQAuthServiceRequests.kt:17` and `ii2/g1.java` `TokenPrecheckInterceptor.kt`):

- **Endpoint**: `POST https://partner-identity.myq-cloud.com/connect/token`
- **Mandatory Refresh Header**:
  ```http
  IsRefresh: true
  Content-Type: application/x-www-form-urlencoded
  BrandId: 1
  App-Version: 5.323.0.1132341
  User-Agent: Pixel 7/Android 14
  ```
- **Form-Urlencoded Payload**:
  ```
  client_id=ANDROID_CGI_MYQ
  &grant_type=refresh_token
  &refresh_token=<stored_refresh_token>
  &scope=MyQ_Residential offline_access
  ```
- **Success Response (200 OK JSON)**:
  ```json
  {
    "access_token": "ey...",
    "refresh_token": "8B...",
    "expires_in": 3600,
    "token_type": "Bearer",
    "scope": "MyQ_Residential offline_access"
  }
  ```
  *Note*: myQ issues rolling refresh tokens. Each refresh call generates a new `refresh_token`; the previous token is immediately invalidated.

### 3.7 Token Lifespan, Expiration & Circuit Breakers
Decompiled from `classes6.dex` (`ii2/g1.java` `TokenPrecheckInterceptor.kt` and `po0/j.java`):

1. **Default Lifespan**: `3600` seconds (1 hour).
2. **Proactive Refresh Algorithm**:
   - Refresh is scheduled at: `Math.max(expires_in * 0.95, expires_in - 300)`.
   - The official client triggers proactive token refresh **5 minutes (300 seconds) before expiration**.
3. **Reactive Refresh**:
   - If a request returns HTTP `401 Unauthorized` with error payload `{"code": "401.209", "message": "Access token has expired."}`, the client executes a single reactive refresh and retries the in-flight request once.
4. **Permanent Rejection Circuit Breakers**:
   - If `/connect/token` returns HTTP `400 Bad Request` with `error: "invalid_grant"`, or error code `"401.102"` (`OAUTH_TOKEN_BAD_REQUEST`):
     - The client immediately invokes `tokenManager.d()` (clears all tokens from local keystore).
     - Terminates all background refresh loops.
     - Never retries username/password logins automatically (to avoid IP bans and account lockouts).

---

## 4. Network Fingerprint & Request Headers

### 4.1 Global Request Headers Table
Decompiled from `classes9.dex` (`z82/b.java:203-209`), `classes6.dex` (`ii2/i0.java`), and `classes3.dex` (`yk/a0.java`):

| Header Name | Format / Value | Example Value | Source / Notes |
|---|---|---|---|
| **`Authorization`** | `Bearer <access_token>` | `Bearer eyJhbGciOi...` | Injected by `HeaderInterceptor.kt` on all API endpoints |
| **`User-Agent`** | `<Build.MODEL>/Android <Build.VERSION.RELEASE>` | `Pixel 7/Android 14` | Decompiled verbatim from `z82/b.java:205` |
| **`BrandId`** | `"1"` (LiftMaster) or `"2"` (Chamberlain) | `1` | `1` for LiftMaster flavor, `2` for Chamberlain flavor (`z82/b.java:206`) |
| **`App-Version`** | `5.323.0.1132341` | `5.323.0.1132341` | Android `versionName` string (`z82/b.java:207`) |
| **`Api-Version`** | `v6.0` | `v6.0` | Explicit API version header (`Lls0/a;->v6_0`) |
| **`Culture`** | `en` (or 2-letter ISO code) | `en` | Device culture / language header (`Culture: en`) |
| **`MyQ-Application-Id`** | Client UUID / Application Identifier | `ANDROID_CGI_MYQ` | Identifies client application flavor |
| **`AntiCSRF`** | `AntiCSRF` | `AntiCSRF` | Static anti-CSRF token header (`us0/a.java`) |
| **`Content-Type`** | `application/json; charset=UTF-8` | `application/json; charset=UTF-8` | For REST command endpoints; `application/x-www-form-urlencoded` for OAuth |
| **`Accept`** | `application/json` | `application/json` | Standard REST response expectation |

### 4.2 Transport & TLS Configuration
Decompiled from `classes6.dex` (`com/chamberlain/myq/sdk/core/network/a.java` lines 245-265, 315-325):

- **TLS Protocols**: `TLSv1.2`, `TLSv1.3` enforced (`SSLContext.getInstance("TLSv1.2")`).
- **ConnectionSpec**: Modern TLS specification (`ConnectionSpec.MODERN_TLS` + `ConnectionSpec.COMPATIBLE_TLS`).
- **HTTP Engine**: OkHttp 4.12+ semantics with HTTP/2 and HTTP/1.1 fallback.
- **Client Timeouts**:
  - Connect Timeout: **20 seconds** (`builder.connectTimeout(20L, TimeUnit.SECONDS)`)
  - Read Timeout: **20 seconds** (`builder.readTimeout(20L, TimeUnit.SECONDS)`)
  - Write Timeout: **20 seconds**
- **Client Identification**: Device installation UUID persisted locally across sessions.

---

## 5. Account & Household Discovery

### 5.1 List Accounts (`GET /api/v6.0/accounts`)
Decompiled from `classes6.dex` (`hm0/b.java` `AccountApi.kt` and `gm0/f.java` `AccountRemoteDataSource.kt`):

- **HTTP Method**: `GET`
- **Path**: `/api/v6.0/accounts`
- **Required Headers**: `Authorization: Bearer <token>`, `BrandId`, `App-Version`, `User-Agent`.
- **Response Schema (`AccountsListDto`)**:
  ```json
  {
    "accounts": [
      {
        "id": "e36e6761-0000-4000-8000-0123456789ab",
        "name": "Home",
        "created_by": "u-4567-89ab-cdef",
        "max_users": {
          "guest": 10,
          "co_owner": 2
        }
      }
    ]
  }
  ```
- **Field Descriptions**:
  - `accounts[].id` (string, UUID): The authoritative `accountId` required for all subsequent device listing and actuation calls.
  - `accounts[].name` (string): Household label configured by the account owner.
  - `accounts[].created_by` (string, UUID): ID of the account owner.
  - `accounts[].max_users`: Permission caps for guest and co-owner sharing.

### 5.2 Account Access & RBAC Permissions (`GET /api/v6.0/accountAccess`)
Decompiled from `classes6.dex` (`hm0/a.java` `AccountAccessApi.kt` and `im0/AccountAccessDto.java`):

- **HTTP Method**: `GET`
- **Path**: `/api/v6.0/accountAccess`
- **Response Schema (`AccountAccessDto`)**:
  ```json
  {
    "account_access": [
      {
        "account_id": "e36e6761-0000-4000-8000-0123456789ab",
        "role": "Owner",
        "permissions": {
          "accounts": ["read", "write"],
          "alerts": ["read", "write"],
          "devices": ["read", "write", "control"],
          "guests": ["read", "write"],
          "history": ["read"],
          "schedules": ["read", "write"],
          "partners": ["read", "write"]
        }
      }
    ]
  }
  ```
- **Roles**: `"Owner"`, `"Co-Owner"`, `"Guest"`. Device command endpoints require the `"control"` permission in the `devices` block.

---

## 6. Device Discovery & Topology

### 6.1 List Account Devices (`GET /api/v6.0/Accounts/{accountId}/Devices`)
Decompiled from `classes6.dex` (`my0/a.java` `DeviceServiceCustom.kt`) and `classes3.dex` (`ac1/DeviceList.java`, `ac1/f.java` `DeviceBase`):

- **HTTP Method**: `GET`
- **Path**: `/api/v6.0/Accounts/{accountId}/Devices`
- **Path Parameters**:
  - `accountId` (UUID string): Extracted from accounts list.
- **Response Schema (`DeviceList`)**:
  ```json
  {
    "href": "https://api.myq-cloud.com/api/v6.0/Accounts/e36e6761-0000-4000-8000-0123456789ab/Devices",
    "count": 2,
    "items": [
      {
        "serial_number": "GW010023ABCD",
        "device_family": "gateway",
        "device_type": "hub",
        "device_model": "MYQ-G0401",
        "device_platform": "Android",
        "name": "Smart Garage Hub",
        "created_date": "2024-01-15T12:00:00.000Z",
        "state": {
          "online": true,
          "wifi_signal_strength": "excellent",
          "firmware_version": "1.32",
          "last_update": "2026-11-08T22:30:00.000Z"
        },
        "specifications": {},
        "capabilities": {}
      },
      {
        "serial_number": "CG0850123456",
        "device_family": "garagedoor",
        "device_type": "virtualgaragedooropener",
        "device_model": "Chamberlain Opener",
        "device_platform": null,
        "name": "Main Garage Door",
        "created_date": "2024-01-15T12:05:00.000Z",
        "state": {
          "parent_serial_number": "GW010023ABCD",
          "door_state": "closed",
          "door_sensor_state": "closed",
          "online": true,
          "dps_low_battery_mode": false,
          "is_unattended_open_allowed": true,
          "is_unattended_close_allowed": true,
          "last_update": "2026-11-08T22:45:00.000Z",
          "active_fault_codes": []
        },
        "specifications": {},
        "capabilities": {}
      }
    ]
  }
  ```

### 6.2 Device Hierarchy & Linkage Architecture
Decompiled from `classes3.dex` (`ac1/f.java` methods `r()` and `s()`):

1. **Gateways (Root Nodes)**:
   - Devices where `device_family.equalsIgnoreCase("gateway")` are hubs or gateways.
   - Typically do not expose physical actuators directly, but carry Wi-Fi telemetry (`wifi_signal_strength`, `firmware_version`).
2. **Child Actuators & Accessories**:
   - Identified by `device_family` values `"garagedoor"`, `"lamp"`, `"locks"`.
   - Child-to-parent linkage is resolved via:
     - Primary: `state.parent_serial_number` (method `s()`)
     - Fallback: `state.gatewayID` or `state.gatewayId` (method `r()`)
3. **Integrated Wi-Fi Openers (Monolithic)**:
   - Certain Wi-Fi garage door openers (`device_type: "wi-fioverheadopener"`) act as both gateway and actuator. In these units, `parent_serial_number` either points to the opener's own serial or is absent.

### 6.3 Device Families & Types Directory
Decompiled from `classes6.dex` (`feature/common/model/g.java` `DeviceFamily.kt` and `o.java` `DeviceType.kt`):

#### Device Families (`device_family`):
- `"gateway"`: Wi-Fi hubs, ethernet gateways, cellular gateways.
- `"garagedoor"`: Garage door openers, commercial operators, gate operators.
- `"lamp"`: Smart lamp modules, wall switches, light controls.
- `"locks"`: Yale / myQ smart deadbolts and levers.
- `"camera"`: myQ smart garage cameras and video keypads.
- `"tend"`, `"hvacs"`, `"petdoor"`, `"routingdevice"`, `"unknown"`.

#### Prominent Device Types (`device_type`):
- **Gateways**: `hub`, `gateway`, `wifigdo`, `cellulargateway`, `ethernetgateway`.
- **Garage Openers**: `garagedooropener`, `wi-figaragedooropener`, `wi-fioverheadopener`, `commercialdooroperator`, `gatemechanism`, `virtualgaragedooropener`, `mysqlintegratedoverheadopener`, `germangaragedooropener`, `frenchgaragedooropener`, `ukgaragedooropener`, `australiangaragedooropener`.
- **Lamps / Lights**: `lampmodule`, `wallswitch`, `lightswitch`, `smartlampmodule`.
- **Locks**: `lock`.

---

## 7. Device State Schemas

### 7.1 Garage Door State (`door_state`)
Decompiled from `classes6.dex` (`feature/common/model/m.java` `DeviceState.kt` and `feature/common/model/devices/t.java` `GarageDoorDevice.kt`):

| Wire Value (`door_state`) | Enum Name | Numeric Code | Intermediate State? | Operational Semantics |
|---|---|---|---|---|
| `"open"` | `OPEN` | `1` | `false` | Door is at upper limit travel, fully opened |
| `"closed"` | `CLOSED` | `2` | `false` | Door is at lower limit travel, fully closed |
| `"stopped"` | `STOPPED` | `3` | `false` | Door motion was stopped mid-travel |
| `"opening"` | `OPENING` | `4` | `true` | Door is actively moving upward toward open position |
| `"closing"` | `CLOSING` | `5` | `true` | Door is actively moving downward toward closed position |
| `"moving"` | `MOVING` | `8` | `true` | Door is in motion; direction unspecified |
| `"autoreverse"` | `AUTO_REVERSE` | `7` | `false` | Safety obstruction triggered automatic reversal |
| *(Hardware State)* | `POSILOCK_DOWN` | `6` | `false` | PosiLock physical down-lock mechanism engaged |
| *(Hardware State)* | `NOT_CLOSED_TILT_SWITCH` | `9` | `false` | Tilt sensor indicates door is not closed |
| *(Hardware State)* | `OFFLINE` | `10` | `false` | Device is disconnected from cloud |
| *(Hardware State)* | `NOT_INITIALIZED` | `0` | `false` | Opener limits/calibration uninitialized |
| *(Fallback)* | `UNKNOWN` | `-1` | `false` | Unrecognized state string |

*Intermediate States Note*: When `isIntermediate = true` (`opening`, `closing`, `moving`), the official app transitions into rapid polling (2–3 second interval) until reaching a stationary state (`open`, `closed`, `stopped`, `autoreverse`).

### 7.2 Lamp / Light State (`lamp_state`)
Decompiled from `classes6.dex` (`feature/common/model/devices/c0.java` `LampDevice.kt`):

| Wire Value (`lamp_state`) | Enum Name | Numeric Code | Semantics |
|---|---|---|---|
| `"On"` / `"on"` | `LAMP_ON` | `21` | Lamp or wall light switched ON |
| `"Off"` / `"off"` | `LAMP_OFF` | `20` | Lamp or wall light switched OFF |
| *(Fallback)* | `UNKNOWN` | `-1` | Unrecognized lamp state |

*Note*: Matching is case-insensitive in the client (`equalsIgnoreCase("On")`).

### 7.3 Door Sensor State (`door_sensor_state`)
Decompiled from `classes3.dex` (`ac1/DeviceAttributes.java`):
- Wire values: `"open"`, `"closed"`.
- Represents the raw position reported by the Door Position Sensor (tilt sensor or optical encoder).

### 7.4 Telemetry, Battery & Diagnostic Attributes
Decompiled from `classes3.dex` (`ac1/DeviceAttributes.java`, `p064st/a.java`, `p064st/l1.java`):

| JSON Field Name | Type | Value Domain / Example | Semantics |
|---|---|---|---|
| `online` | `boolean` | `true` / `false` | Gateway / Opener cloud connectivity status |
| `wifi_signal_strength` | `string` | `"excellent"`, `"good"`, `"fair"`, `"weak"`, `"unknown"`, or dBm integer string (e.g. `"-68"`) | Wi-Fi RSSI level |
| `battery_percentage` | `string` | `"0"` to `"100"` | Battery backup charge level percentage |
| `battery_voltage` | `string` | `"12.4"` | Backup battery terminal voltage |
| `battery_backup_state` | `string` | `"Idle"`, `"Charging"`, `"Bad"`, `"Disconnected"`, `"none"`, `"Unknown"` | Battery backup operational state (`p064st/a.java`) |
| `dps_low_battery_mode` | `boolean` | `true` / `false` | Door Position Sensor (tilt sensor) battery low flag |
| `low_battery` | `boolean` | `true` / `false` | Generic peripheral battery low warning |
| `sensor_comm_error` | `boolean` | `true` / `false` | RF communication lost between opener and door sensor |
| `attached_work_light_error_present` | `boolean` | `true` / `false` | Opener work light circuit fault |
| `is_unattended_open_allowed` | `boolean` | `true` / `false` | UL 325 safety flag: remote unattended open enabled |
| `is_unattended_close_allowed` | `Boolean` | `true` / `false` | UL 325 safety flag: remote unattended close enabled |
| `firmware_version` | `string` | `"1.32"` | Installed firmware revision |
| `software_version` | `string` | `"5.323.0"` | Installed software revision |
| `last_update` | `string` | `"2026-11-08T22:45:00.000Z"` | ISO 8601 UTC timestamp of last state report |
| `last_is_online_change` | `string` | `"2026-11-08T20:10:00.000Z"` | ISO 8601 UTC timestamp of last online/offline transition |
| `updated_date` | `string` | `"2026-11-08T22:45:00.000Z"` | ISO 8601 UTC timestamp of last device update |
| `command_execution` | `object` | `{"command": "close", "status": "executing"}` | In-flight command tracking block |

### 7.5 Active Fault Codes Schema
Decompiled from `classes3.dex` (`ac1/FaultCode.java`):

When an opener or gateway experiences hardware or safety faults, `state.active_fault_codes` contains an array of fault objects:
```json
[
  {
    "code": "310",
    "description": "Safety reversal obstruction detected",
    "activated_timestamp": "2026-11-08T22:44:12.000Z"
  }
]
```
If no faults are present, `active_fault_codes` is empty (`[]`) or omitted.

---

## 8. Command Endpoints, Actions & Payloads

All command endpoints execute against the primary API Gateway (`https://api.myq-cloud.com`) using API version `v6.0`.

### 8.1 Garage Door Open (`PUT .../open`)
Decompiled from `classes6.dex` (`cz0/a.java:f` and `bz0/y.java:Y`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/open`
- **Headers**:
  ```http
  Authorization: Bearer <access_token>
  Content-Type: application/json; charset=UTF-8
  BrandId: 1
  App-Version: 5.323.0.1132341
  User-Agent: Pixel 7/Android 14
  Api-Version: v6.0
  Culture: en
  ```
- **Request Body**: **Empty (0 bytes / null)**.
- **Expected Success Status**: `200 OK` or `204 No Content` (Empty body).

### 8.2 Garage Door Close (`PUT .../close`)
Decompiled from `classes6.dex` (`cz0/a.java:j` and `bz0/y.java:C`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/close`
- **Headers**: Same as Door Open.
- **Request Body**: **Empty (0 bytes / null)**.
- **Expected Success Status**: `200 OK` or `204 No Content` (Empty body).
- **Physical Safety Note**: Initiation of this command activates the physical opener's 5-second audible beeper and flashing light sequence prior to mechanical movement (UL 325 unattended operation requirement).

### 8.3 Lamp ON (`PUT .../lamps/{serialNumber}/on`)
Decompiled from `classes6.dex` (`vz0/a.java:c` and `uz0/e.java:n`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/lamps/{serialNumber}/on`
- **Headers**: Same as Door Open.
- **Request Body**: **Empty (0 bytes / null)**.
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.4 Lamp OFF (`PUT .../lamps/{serialNumber}/off`)
Decompiled from `classes6.dex` (`vz0/a.java:d` and `uz0/e.java:l`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/lamps/{serialNumber}/off`
- **Headers**: Same as Door Open.
- **Request Body**: **Empty (0 bytes / null)**.
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.5 Opener Worklight Settings
Decompiled from `classes6.dex` (`cz0/a.java:d` and `dz0/j.java`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/worklight_settings`
- **Request Body (JSON)**:
  ```json
  {
    "enable_pir_control": true,
    "enable_motion_wl": true
  }
  ```
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.6 Opener Light Auto-Off Timer
Decompiled from `classes6.dex` (`cz0/a.java:e` and `dz0/e.java`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/lighttimer`
- **Request Body (JSON)**:
  ```json
  {
    "light_timer_setting": 300
  }
  ```
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.7 Opener Vacation / Lock Mode
Decompiled from `classes6.dex` (`cz0/a.java:c` and `dz0/k.java`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/lockmode`
- **Request Body (JSON)**:
  ```json
  {
    "enable_lock_mode": true
  }
  ```
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.8 Opener Time to Close (TTC)
Decompiled from `classes6.dex` (`cz0/a.java:v` and `dz0/h.java`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}/timetoclose`
- **Request Body (JSON)**:
  ```json
  {
    "time_to_close_setting": 600
  }
  ```
- **Expected Success Status**: `200 OK` or `204 No Content`.

### 8.9 Smart Lock Actuation & Auto-Relock
Decompiled from `classes6.dex` (`k01/a.java` and `i01/n.java`):

- **Lock Deadbolt**: `POST /api/v6.0/accounts/{accountId}/devices/locks/{serialNumber}/lock?requestRemote=true` (Empty body)
- **Unlock Deadbolt**: `POST /api/v6.0/accounts/{accountId}/devices/locks/{serialNumber}/unlock?requestRemote=true` (Empty body)
- **Auto-Relock Configuration**:
  `PUT /api/v6.0/accounts/{accountId}/devices/locks/{serialNumber}/auto_relock?requestRemote=true`
  ```json
  {
    "auto_relock_enabled": true,
    "auto_relock_time": "30"
  }
  ```

### 8.10 Device Rename
Decompiled from `classes6.dex` (`vz0/a.java:b`, `cz0/a.java:b`, `zx0/a.java`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v6.0/accounts/{accountId}/door_openers/{serialNumber}` or `.../lamps/{serialNumber}`
- **Request Body (JSON)**:
  ```json
  {
    "name": "Side Garage Door"
  }
  ```

### 8.11 Legacy Fallback Endpoint (`v5.1`)
Decompiled from `classes6.dex` (`ny0/b.java` `LegacyDeviceActionDataSource.kt`):

- **HTTP Method**: `PUT`
- **Path**: `/api/v5.1/Accounts/{accountId}/Devices/{deviceId}/actions`
- **Request Body (JSON)**:
  ```json
  {
    "action_type": "open"
  }
  ```
  *(Supported `action_type` values: `"open"`, `"close"`, `"turn_on"`, `"turn_off"`).*

---

## 9. Error Response Schemas & Status Codes

### 9.1 Error JSON Response Formats
Decompiled from `classes6.dex` (`vs0/k$a.java` and `vs0/k$b.java` `RestObject.java`):

The backend delivers error responses in one of three JSON formats:

#### Format A: Nested Error Object
```json
{
  "errors": {
    "code": "310",
    "description": "Safety sensor obstruction detected",
    "message": "The door cannot be moved while an obstruction is detected.",
    "owning_partner": {
      "name": "Chamberlain"
    }
  }
}
```

#### Format B: Flat Error Object
```json
{
  "code": "-9990",
  "description": "Device unreachable",
  "message": "Gateway error communicating with device."
}
```

#### Format C: OAuth2 / Identity Error Object
```json
{
  "errorId": "b18742b7-08e1-4566-a36c-94d30a84d4fb",
  "code": "401.209",
  "message": "Access token has expired."
}
```

### 9.2 Error Code Mapping Directory
Decompiled from `RestObject.java` (`vs0/k$a;` methods `j` through `t` and `vs0/k$b;->v`):

| Error Code | HTTP Status | Meaning | Integration Action |
|---|---|---|---|
| `0` | `200` / `204` | Success / No Error | Success |
| `127` | `404` | Device Not Associated with Account | Invalidate device cache; re-run device discovery |
| `138` | `502` | Device / Gateway Offline | Mark entity as `unavailable` in Home Assistant |
| `302` | `403` | Session / Account Mismatch | Re-authenticate account session |
| `309` | `429` | Device Busy / Command Rate Limited | Throttle commands; apply exponential backoff |
| `310` | `409` | Safety Beam Obstruction / Door in Motion | Reject command; set problem binary sensor to `on` |
| `321` | `409` | Transition State Conflict (e.g. already closed) | Refresh device state; suppress redundant commands |
| `401.102` | `400` / `401` | OAuth Bad Request / Invalid Refresh Token | Trigger fatal auth circuit breaker; abort retry |
| `401.209` | `401` | Access Token Expired | Perform reactive token refresh and replay command |
| `404` | `404` | Device or Account Endpoint Not Found | Check serial number and account UUID |
| `500` | `500` | Internal Cloud Error | Transient failure; retry after jitter delay |
| `502` | `502` | Cloud Gateway Communication Timeout | Mark command as unconfirmed; refresh status |
| `-9990` | `502` | Generic Device Communication Failure | Transient device RF failure; refresh status |

---

## 10. Safety Invariants & Client Timing Rules

When implementing an integration against this protocol, the following physical and regulatory safety invariants must be enforced:

1. **Idempotent Actuation Gating**:
   - `open`: If current `door_state` is `"open"` or `"opening"`, the client must send **0 network requests** and return immediately.
   - `close`: If current `door_state` is `"closed"` or `"closing"`, the client must send **0 network requests** and return immediately.

2. **Timeout Non-Retry Rule**:
   - If an HTTP `PUT` open or close command times out after the TCP/TLS request has been transmitted, the client **must never retry the command payload**. Re-transmitting an ambiguous command risks cycling a moving door.
   - The client must schedule an immediate status refresh to observe actual mechanical movement.

3. **Stall & Reversal Detection**:
   - If the door encounters an obstruction, the hardware automatically halts and reverses, reporting `door_state: "autoreverse"`. The client must set its problem binary sensor to `on`.
   - If a door command is sent and the door does not reach its target state within **45 seconds**, the client must flag a timeout problem condition.

4. **Polling Cadence & Jitter**:
   - **Normal Polling**: Default interval is **180 seconds with ±20 seconds of random jitter** (configurable between 60s and 600s).
   - **Fast Polling Post-Command**: Following any actuation command (`open` or `close`), the coordinator enters fast polling at **5-second intervals for up to 35 seconds** to track transit progress without overwhelming the cloud rate limiters.

5. **Rate Limiting & Anti-Lockout**:
   - All token refresh operations must be guarded by an atomic single-flight lock (`asyncio.Lock`).
   - If a refresh token request fails with `invalid_grant` or `401.102`, background polling must immediately stop and raise an authentication exception. The client must never attempt automatic username/password logins.

---

## 11. Decompiled Source & Bytecode Index

All findings in this document can be independently reproduced from the disassembled APK bytecode using the following file and symbol references:

| Specification Component | DEX File | Decompiled Java / Smali Path | Key Methods / Symbols |
|---|---|---|---|
| **Identity / OAuth Config** | N/A | `assets/myq-config.json` | `myQAppId`, `myQResidentialScope`, `connectProduction` |
| **OAuth PKCE Generator** | `classes6.dex` | `po0/j.java` (`AuthManagerImpl.kt`) | `z0()`, code challenge Base64 flags `11`, `SecureRandom` |
| **OAuth Retrofit Routes** | `classes6.dex` | `po0/s.java` (`MyQAuthServiceRequests.kt`) | `@o("/connect/token")`, `@k({"IsRefresh: true"})` |
| **Webview DOM Selectors** | `classes6.dex` | `po0/j.java` (`AuthManagerImpl.kt`) | `document.getElementById('Email')`, `'Password'`, `'btn-signin'` |
| **Header Interceptor** | `classes9.dex` | `z82/b.java` (`NetworkClient.java`) | `s()`: `User-Agent`, `BrandId`, `App-Version` |
| **Brand Constants** | `classes3.dex` | `yk/a0.java` (`MyQBrandConstants.kt`) | `BRAND_ID = 1`, `BRAND = "Liftmaster"` |
| **API Version Enum** | `classes6.dex` | `ii2/a.java` / `ls0/a.java` (`ApiVersion.kt`) | `v6_0 = new a("v6.0", 0, "v6.0")` |
| **Account API** | `classes6.dex` | `hm0/b.java` (`AccountApi.kt`) | `@f("api/{apiVersion}/accounts")` |
| **Account DTO** | `classes6.dex` | `im0/AccountsListDto.java`, `im0/Account.java` | `id`, `name`, `created_by`, `max_users` |
| **Device Discovery API** | `classes6.dex` | `my0/a.java` (`DeviceServiceCustom.kt`) | `@f("/api/{apiVersion}/Accounts/{accountId}/Devices")` |
| **Device Hierarchy** | `classes3.dex` | `ac1/f.java` (`DeviceBase.java`) | `s()` -> `parent_serial_number`, `r()` -> `gatewayID` |
| **Door Opener Commands** | `classes6.dex` | `cz0/a.java` (`DoorActionApi.kt`) | `f`: `.../open`, `j`: `.../close` |
| **Lamp Commands** | `classes6.dex` | `vz0/a.java` (`LampApi.kt`) | `c`: `.../on`, `d`: `.../off` |
| **Door State Parser** | `classes6.dex` | `feature/common/model/devices/t.java` | `d(str)`: `"open"`, `"closed"`, `"opening"`, `"closing"` |
| **Lamp State Parser** | `classes6.dex` | `feature/common/model/devices/c0.java` | `a(str)`: `"On"`, `"Off"` |
| **Device State Enum** | `classes6.dex` | `feature/common/model/m.java` (`DeviceState.kt`) | `OPEN(1)`, `CLOSED(2)`, `STOPPED(3)`, `AUTO_REVERSE(7)` |
| **Device Attributes DTO** | `classes3.dex` | `ac1/DeviceAttributes.java` | `online`, `wifi_signal_strength`, `battery_percentage`, faults |
| **Fault Codes DTO** | `classes3.dex` | `ac1/FaultCode.java` | `code`, `description`, `activated_timestamp` |
| **Error Parser** | `classes6.dex` | `vs0/k$a.java`, `vs0/k$b.java` (`RestObject.java`) | Error codes `310`, `321`, `309`, `138`, `401.209`, `-9990` |
| **Token Interceptor** | `classes6.dex` | `ii2/g1.java` (`TokenPrecheckInterceptor.kt`) | Proactive refresh (300s window), `401.102` circuit breaker |

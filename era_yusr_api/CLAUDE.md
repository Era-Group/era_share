# CLAUDE.md — Yusr HR Mobile App Project

> **Purpose of this file**
> This document hands off the Yusr project to Claude Code (or any agentic
> coding assistant) with **everything needed to continue work without
> additional clarification**: context, architecture, API contract, current
> status, and a prioritized roadmap.
>
> **Drop this file at the root of every repo** in the project (both backend
> and mobile) so Claude Code picks it up automatically.

---

## 1. Project Overview

**Yusr** (يُسر — "ease/convenience" in Arabic) is an integrated HR
self-service mobile application for **Era Group** (Saudi Arabia,
https://era.net.sa). It replaces paper-based HR processes with a digital
experience backed by Odoo 18 ERP.

### Goals
- Let employees manage attendance, leaves, payslips, and expenses from their phone
- Reduce HR administrative load
- Enforce geofenced check-in for accurate attendance
- Bilingual Arabic (RTL) / English, Arabic as default

### Users
- **Employee** — self-service across all modules
- **Manager** — approves team leaves and expenses, views team attendance
- **HR Admin** — broadcasts, configuration, escalations

### Official Documentation Source
The app spec was originally published at:
https://yusr-app-doc.vercel.app/

---

## 2. Repository Layout

The project consists of **two separate Git repositories**:

| Repo                 | Purpose                                    | Status         |
|----------------------|--------------------------------------------|----------------|
| `era_yusr_api`       | Odoo 18 backend module (REST API)          | ✅ COMPLETE    |
| `yusr-mobile`        | React Native (Expo) mobile app             | 🚧 TO BE BUILT |

Both repos should live under the `Era-Group` GitHub organization.

---

## 3. Current Status (as of 2026-04-19)

### ✅ Done
- **Backend module `era_yusr_api`** — fully implemented and syntax-verified
  - Employee ID + PIN authentication (no portal/internal-user login)
  - JWT access (8 h) + refresh (30 d) tokens
  - PIN hashing via `werkzeug.security` PBKDF2-SHA256
  - Account lockout after 5 failed attempts (15 min)
  - 20+ REST endpoints (see §6 API Contract)
  - CORS preflight handler
  - HR admin wizard to set PINs (`yusr.set.pin.wizard`)
  - Geofenced check-in using Haversine distance
  - Push notification device registry (`yusr.device`)

### 🚧 Pending
1. **Mobile app (`yusr-mobile`)** — entire React Native/Expo app
2. **Push notification sender** — server-side Firebase/Expo Push integration
3. **End-to-end tests** for the API
4. **Postman collection** for easier manual testing
5. **CI/CD** — GitHub Actions for Odoo module linting + (later) mobile builds

---

## 4. Critical Design Decisions (DO NOT CHANGE WITHOUT DISCUSSION)

1. **Login is Employee ID + PIN ONLY.** No email/password, no Odoo portal
   login, no OAuth. This is a hard product requirement. Never suggest
   refactoring it to reuse `res.users` authentication.

2. **PINs are 4–6 digits, numeric only.** Stored as PBKDF2 hash. Never
   logged in plaintext anywhere.

3. **Geofencing is server-side enforced.** The mobile app sends GPS
   coordinates; the backend computes distance from company location and
   rejects if outside radius. Never trust client-side geofencing alone.

4. **Profile edits go through HR approval.** Direct writes to `hr.employee`
   from the mobile app are not allowed. `PUT /api/yusr/profile` posts a
   chatter message requesting the change.

5. **Arabic RTL is first-class.** All UI flows must work cleanly in RTL.
   Arabic is the default language. The app serves Saudi Arabia.

---

## 5. Backend: `era_yusr_api` (Odoo 18 Module)

### 5.1 Module Structure
```
era_yusr_api/
├── __init__.py
├── __manifest__.py
├── README.md
├── controllers/        REST API (see §6)
│   ├── base.py         @yusr_authenticated decorator, JSON helpers, CORS
│   ├── auth.py         login / refresh / logout / forgot-pin
│   ├── profile.py
│   ├── attendance.py   with Haversine geofence
│   ├── leaves.py
│   ├── payslips.py
│   ├── expenses.py
│   ├── schedule.py
│   ├── calendar_ctrl.py
│   ├── hr_requests.py
│   └── device.py       push notification token registration
├── models/
│   ├── hr_employee.py  adds employee_login_id, pin_hash, lockout fields
│   ├── res_config_settings.py
│   └── yusr_device.py
├── wizard/
│   └── set_pin_wizard.py
├── utils/
│   └── jwt_helper.py
├── views/
│   ├── hr_employee_views.xml
│   └── res_config_settings_views.xml
├── data/
│   └── ir_config_parameter_data.xml
└── security/
    └── ir.model.access.csv
```

### 5.2 Dependencies
- **Odoo 18.0** (Community or Enterprise)
- Odoo modules: `hr`, `hr_attendance`, `hr_holidays`, `hr_expense`, `mail`,
  `calendar`, `resource`
- Optional: `hr_payroll` (Enterprise only) — payslip endpoints degrade
  gracefully if not installed
- Python: `PyJWT` (declared in `external_dependencies`)

### 5.3 Installation
```bash
pip install PyJWT
# copy module to addons dir, then:
./odoo-bin -c odoo.conf -d DB_NAME -u era_yusr_api --stop-after-init
```

### 5.4 Required Configuration After Install
1. **Settings → Yusr Mobile API → Generate Strong Secret** (JWT secret).
2. Set `res.company.partner_id.partner_latitude/longitude` for geofencing.
3. For each employee:
   - Fill `employee_login_id` (unique, e.g. `EMP1023`)
   - Toggle `yusr_access_enabled`
   - Use **Set Yusr PIN** action to assign initial PIN

### 5.5 Key Model Extensions

**`hr.employee`** — new fields:
| Field                   | Type     | Notes                                    |
|-------------------------|----------|------------------------------------------|
| `employee_login_id`     | Char     | unique, alphanumeric+`_-`, 3-32 chars    |
| `pin_hash`              | Char     | PBKDF2 hash, readable only by HR group   |
| `yusr_access_enabled`   | Boolean  | per-employee kill switch                 |
| `yusr_last_login`       | Datetime | stamped on successful login              |
| `yusr_failed_attempts`  | Integer  | resets on success; triggers lockout at 5 |
| `yusr_locked_until`     | Datetime | lockout expiry                           |

Methods:
- `set_pin(pin)` — validates format, hashes, stores
- `verify_pin(pin)` — constant-time check
- `authenticate_yusr(login_id, pin)` — **@api.model**, handles full login flow incl. lockout

### 5.6 Testing the Backend Manually

Login:
```bash
curl -X POST https://erp.era.net.sa/api/yusr/auth/login \
  -H "Content-Type: application/json" \
  -d '{"employee_id":"EMP1023","pin":"1234"}'
```

Check profile:
```bash
curl https://erp.era.net.sa/api/yusr/profile \
  -H "Authorization: Bearer <access_token_from_login>"
```

---

## 6. API Contract (Source of Truth for Mobile App)

**Base URL**: `https://erp.era.net.sa` (production) — configurable per env.

All requests/responses are JSON. All authenticated endpoints require:
```
Authorization: Bearer <access_token>
```

All successful responses follow:
```json
{ "success": true, "data": { ... } }
```

All errors follow:
```json
{ "success": false, "error": "message", "code": "ERROR_CODE" }
```

### 6.1 Authentication

#### `POST /api/yusr/auth/login`
**Public.** Body:
```json
{ "employee_id": "EMP1023", "pin": "1234" }
```
Success (200):
```json
{
  "success": true,
  "data": {
    "token": "eyJ...",
    "refresh_token": "eyJ...",
    "expires_in": 28800,
    "token_type": "Bearer",
    "employee": {
      "id": 42,
      "name": "Ahmed Ali",
      "login_id": "EMP1023",
      "job_title": "Accountant",
      "department": "Finance",
      "manager": "Sara Ahmed",
      "avatar_url": "/web/image/hr.employee/42/image_128",
      "roles": ["employee", "manager"]
    }
  }
}
```
Errors: `401 INVALID_CREDENTIALS` (wrong PIN / locked / disabled).

#### `POST /api/yusr/auth/refresh`
Body: `{ "refresh_token": "..." }` → returns new access + refresh tokens.

#### `POST /api/yusr/auth/logout`
Authenticated. Client-side logout (clears tokens locally). Server logs event.

#### `POST /api/yusr/auth/forgot-pin`
Public. Body: `{ "employee_id": "EMP1023" }` → notifies HR. Always returns
success to prevent user enumeration.

### 6.2 Profile
- `GET /api/yusr/profile` — full profile
- `PUT /api/yusr/profile` — body with allowed fields (`mobile_phone`,
  `work_phone`, `private_phone`, `emergency_contact`, `emergency_phone`,
  `private_street`, `private_city`, `private_zip`). Posts to chatter, NOT
  direct write.

### 6.3 Attendance
- `POST /api/yusr/attendance/checkin` — body: `{ latitude, longitude, override_reason? }`
  - Returns `403 OUTSIDE_GEOFENCE` with `distance_meters` if outside radius
    and no `override_reason` supplied.
  - Returns `409 ALREADY_CHECKED_IN` if open attendance exists.
- `POST /api/yusr/attendance/checkout` — body: `{ latitude, longitude }`
  - Returns `404 NO_OPEN_ATTENDANCE` if nothing to close.
- `GET  /api/yusr/attendance/status` — current state + today's totals
- `GET  /api/yusr/attendance/records?month=YYYY-MM` — month records

### 6.4 Leaves
- `GET  /api/yusr/leaves/balances` — array of `{id, name, allocated, taken, remaining}`
- `GET  /api/yusr/leaves/types`
- `GET  /api/yusr/leaves/requests` — history (last 100)
- `POST /api/yusr/leaves/requests` — body: `{ holiday_status_id, date_from, date_to, reason?, half_day? }`
- `POST /api/yusr/leaves/requests/<id>/cancel`

### 6.5 Payslips (requires `hr_payroll`)
- `GET /api/yusr/payslips`
- `GET /api/yusr/payslips/<id>` — with line items
- `GET /api/yusr/payslips/<id>/pdf` — returns `application/pdf` bytes

### 6.6 Expenses
- `GET  /api/yusr/expenses`
- `GET  /api/yusr/expenses/categories`
- `POST /api/yusr/expenses` — body: `{ name, product_id, total_amount, date?, description?, currency_id?, receipt_base64?, receipt_filename? }`

### 6.7 Schedule & Calendar
- `GET /api/yusr/schedule` — weekly work schedule from `resource.calendar`
- `GET /api/yusr/calendar?month=YYYY-MM` — unified: leaves + public holidays + personal events

### 6.8 HR Communication
- `GET  /api/yusr/hr/requests` — recent chatter messages on employee
- `POST /api/yusr/hr/requests` — body: `{ subject?, body, category? }`

### 6.9 Push Device
- `POST   /api/yusr/device/register` — body: `{ device_token, platform, app_version? }`
- `DELETE /api/yusr/device/unregister` — body: `{ device_token }`

---

## 7. Mobile App: `yusr-mobile` (TO BE BUILT)

### 7.1 Recommended Tech Stack
- **Expo SDK 51+** with **Expo Router**
- **TypeScript** (strict mode)
- **Zustand** for client state
- **TanStack Query (React Query)** for server state + caching
- **Axios** with interceptors for JWT injection + auto-refresh on 401
- **expo-secure-store** — JWT storage (NEVER AsyncStorage for tokens)
- **expo-local-authentication** — biometrics
- **expo-location** — GPS for check-in
- **expo-notifications** — push
- **expo-image-picker** + **expo-camera** — receipt capture
- **react-native-calendars** — calendar views
- **react-native-maps** — check-in location preview
- **react-i18next** — AR/EN localization

### 7.2 Project Structure (Suggested)
```
yusr-mobile/
├── app/                         (Expo Router — file-based routes)
│   ├── (auth)/
│   │   ├── login.tsx            Employee ID + PIN screen
│   │   ├── biometric-setup.tsx
│   │   └── forgot-pin.tsx
│   ├── (tabs)/
│   │   ├── _layout.tsx          bottom tab nav
│   │   ├── home.tsx             dashboard
│   │   ├── attendance.tsx       check-in/out + records
│   │   ├── leaves.tsx
│   │   ├── payslips.tsx
│   │   └── more.tsx             expenses, schedule, calendar, HR chat, settings
│   ├── _layout.tsx              root layout with auth gate
│   └── +not-found.tsx
├── src/
│   ├── api/
│   │   ├── client.ts            axios instance, interceptors
│   │   ├── endpoints.ts         typed endpoint functions
│   │   └── types.ts             API response types (mirror §6)
│   ├── stores/
│   │   ├── auth.ts              zustand auth store
│   │   └── settings.ts
│   ├── components/
│   │   ├── ui/                  primitives: Button, Input, Card, Badge, etc.
│   │   ├── AttendanceWidget.tsx
│   │   ├── LeaveBalanceCard.tsx
│   │   └── ...
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── useBiometric.ts
│   │   └── useGeolocation.ts
│   ├── i18n/
│   │   ├── index.ts
│   │   ├── ar.json              DEFAULT
│   │   └── en.json
│   ├── theme/
│   │   ├── colors.ts            primary #1A3A5C, accent #D4A84B
│   │   └── typography.ts        Cairo/IBM Plex Arabic + Inter
│   └── utils/
│       ├── secureStore.ts       wrappers around expo-secure-store
│       └── formatters.ts        dates, currencies (SAR)
├── assets/
├── .env.example
└── package.json
```

### 7.3 Authentication Flow (CRITICAL)

```
┌──────────────────────────────────────────────────────────────────┐
│ FIRST LAUNCH                                                     │
│   1. Show login screen: Employee ID + PIN                        │
│   2. POST /api/yusr/auth/login                                   │
│   3. Store access_token + refresh_token in expo-secure-store     │
│   4. Store employee profile in zustand                           │
│   5. Prompt: "Enable Face ID / Fingerprint for faster login?"    │
│      - if yes → store a "biometric_enabled" flag in secure-store │
│   6. Prompt: "Set a 6-digit app passcode?" (fallback for biometric)│
│      - if yes → store hashed passcode in secure-store            │
│   7. Navigate to home                                            │
├──────────────────────────────────────────────────────────────────┤
│ SUBSEQUENT LAUNCHES                                              │
│   - If access_token valid (not expired): go straight to home     │
│   - If expired but refresh_token valid:                          │
│     • Require biometric OR app passcode                          │
│     • On success: POST /api/yusr/auth/refresh                    │
│     • Replace tokens                                             │
│   - If refresh_token expired/invalid: back to login screen       │
├──────────────────────────────────────────────────────────────────┤
│ BACKGROUND / FOREGROUND                                          │
│   - Require biometric OR passcode after >5 min backgrounded      │
├──────────────────────────────────────────────────────────────────┤
│ AUTO-LOGOUT                                                      │
│   - After 15 min of inactivity, require re-auth                  │
└──────────────────────────────────────────────────────────────────┘
```

**Axios interceptor pattern**:
```typescript
// On 401, try refresh once; if that fails, clear tokens and navigate to login.
apiClient.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response?.status === 401 && !error.config._retry) {
      error.config._retry = true;
      const refreshed = await tryRefreshToken();
      if (refreshed) {
        error.config.headers.Authorization = `Bearer ${refreshed}`;
        return apiClient.request(error.config);
      }
      await logout();
    }
    return Promise.reject(error);
  }
);
```

### 7.4 UI / Design System
- **Primary color**: `#1A3A5C` (deep navy — Era Group brand)
- **Accent**: `#D4A84B` (gold)
- **Arabic font**: Cairo or IBM Plex Sans Arabic
- **English font**: Inter
- **RTL**: force at app startup via `I18nManager.forceRTL(true)` when locale is `ar`
- **Bottom tabs**: Home, Attendance, Leaves, Payslips, More
- **Home dashboard** (priority):
  - Greeting + current date (Arabic/Gregorian)
  - Big Check-In / Check-Out button (state-aware)
  - Today status card (check-in time, elapsed hours)
  - Leave balance quick view (Annual, Sick)
  - Recent activity / notifications
  - Shortcuts: Submit Leave, Submit Expense, HR Chat

### 7.5 Screens to Build (Priority Order)
1. **Login (Employee ID + PIN)** — ⚡ CRITICAL FIRST
2. **Home Dashboard**
3. **Attendance** — check-in/out with GPS, map preview, today/month view
4. **Leaves** — balances + submit + history
5. **Payslips** — list + detail + PDF view (with biometric re-auth gate)
6. **Expenses** — list + submit with receipt photo
7. **Profile** — view + edit (edit = request flow)
8. **Settings** — language, biometric toggle, logout
9. **HR Chat** — simple thread UI
10. **Schedule & Calendar** — weekly + monthly views
11. **Manager extensions** — team attendance, approve leaves/expenses
12. **Biometric + passcode setup flows**

### 7.6 Screenshot Security
On `payslips/*` screens, prevent screenshots:
```typescript
// Android
import * as ScreenCapture from 'expo-screen-capture';
useEffect(() => {
  ScreenCapture.preventScreenCaptureAsync();
  return () => { ScreenCapture.allowScreenCaptureAsync(); };
}, []);
```

### 7.7 Environment Variables (`.env.example`)
```
EXPO_PUBLIC_API_BASE_URL=https://erp.era.net.sa
EXPO_PUBLIC_MOCK_API=false
EXPO_PUBLIC_SENTRY_DSN=
```

---

## 8. Roadmap / Next Steps (Prioritized)

### Phase 1 — Mobile MVP (2-3 weeks)
- [ ] Scaffold Expo + TypeScript + Expo Router project
- [ ] Configure i18next with AR (default) + EN, force RTL for AR
- [ ] Build theme, typography, and base UI primitives
- [ ] Implement API client with axios + JWT interceptors
- [ ] Login screen (Employee ID + PIN) end-to-end with real API
- [ ] Secure token storage + biometric setup
- [ ] Home dashboard
- [ ] Attendance check-in/out flow with GPS + geofence handling

### Phase 2 — Core HR Features (2 weeks)
- [ ] Leaves module (balances, submit, history, cancel)
- [ ] Payslips module (list, detail, PDF view, screenshot block)
- [ ] Expenses module (list, submit with camera receipt)
- [ ] Profile view + update request flow

### Phase 3 — Supporting Features (1-2 weeks)
- [ ] Schedule + Calendar views
- [ ] HR chat / request thread
- [ ] Push notification registration
- [ ] Push delivery — server-side (Firebase/Expo Push integration in a new
      Odoo module or extension to `era_yusr_api`)
- [ ] Settings screen (language, logout, reset PIN request)

### Phase 4 — Manager & HR Admin (1 week)
- [ ] Team tab for managers
- [ ] Approve/refuse team leaves, expenses
- [ ] HR Admin broadcast messages

### Phase 5 — Hardening
- [ ] E2E tests (Detox or Maestro)
- [ ] Sentry error tracking
- [ ] CI/CD — EAS Build for iOS/Android
- [ ] App Store + Google Play submission
- [ ] Odoo backend: add rate limiting per IP at reverse proxy
- [ ] Token blacklist table for true server-side logout revocation

---

## 9. Known Issues & Gotchas

1. **`hr_payroll` is Enterprise.** Payslip endpoints check `if 'hr.payslip'
   in request.env` and degrade gracefully. Don't assume Enterprise.

2. **`partner_latitude`/`partner_longitude` must be set on company partner**
   for geofencing. If unset, geofence check silently passes (intentional
   fallback to avoid breaking check-in).

3. **`hr.attendance` in Odoo 18 has `in_latitude/in_longitude`** fields
   (not `latitude_in`). Already handled in `controllers/attendance.py`.

4. **Odoo 18 removed `hr_contract` from Community Edition.** Any code that
   queried contracts needs to gate on Enterprise or use `first_contract_date`
   directly on `hr.employee` (already done).

5. **RTL iOS quirk**: `I18nManager.forceRTL(true)` requires app restart
   to take effect. Handle with a first-launch restart via
   `expo-updates` `reloadAsync()`.

6. **Biometric on Android**: `expo-local-authentication` needs
   `USE_BIOMETRIC` permission — added automatically by the plugin, but
   confirm after any SDK upgrade.

7. **Token storage size**: JWT tokens can exceed iOS Keychain's
   default item size in rare cases. `expo-secure-store` handles this,
   but don't pack extra claims into JWT.

8. **Login IDs are case-insensitive on lookup** (`=ilike`) but stored
   as-entered. Trim whitespace client-side before sending.

9. **Geofence radius default is 200 m.** Too tight for some office
   configurations. Document the Settings screen for HR to adjust.

10. **Odoo 18 leave flow**: creating a leave via `hr.leave.create()` does
    not auto-confirm. Code explicitly calls `leave.action_confirm()`.

---

## 10. Code Conventions

### Backend (Python/Odoo)
- Follow OCA style guide + PEP 8
- `_logger = logging.getLogger(__name__)` at top of every controller
- Always use `sudo()` deliberately and comment why when non-obvious
- Never log PINs, tokens, or password hashes
- Endpoint handlers: `@yusr_authenticated` decorator injects `employee` kwarg
- Error responses: use `err(message, status, code)` from `controllers/base.py`
- Success responses: use `ok(data)` from `controllers/base.py`

### Mobile (TypeScript)
- Strict TypeScript, `noImplicitAny: true`
- Server state: TanStack Query only (no useState for API data)
- Client state: Zustand (no Redux)
- File-based routing (Expo Router) — no manual navigation stacks
- All user-facing strings via `t('key')` — no hardcoded literals
- Currency: always show SAR with proper locale formatting
- Dates: use `date-fns` with `ar-SA` or `en-US` locale

---

## 11. How to Continue Work

### If picking up the backend:
```bash
git clone https://github.com/Era-Group/era_yusr_api.git
cd era_yusr_api
# read README.md
# run syntax check:
python3 -m py_compile $(find . -name "*.py")
```
Priority TODOs: add unit tests under `tests/`, add OpenAPI/Swagger spec,
wire up rate limiting.

### If starting the mobile app:
```bash
npx create-expo-app yusr-mobile --template
cd yusr-mobile
# install core deps
npx expo install expo-router expo-secure-store expo-local-authentication \
  expo-location expo-notifications expo-image-picker expo-camera
npm install zustand @tanstack/react-query axios react-i18next i18next \
  react-native-calendars react-native-maps
```
Begin with §7.3 authentication flow + §7.5 screen priority list.

### When in doubt:
- API contract is in §6 above — this is the source of truth
- Backend code is the fallback source of truth — read the controller file
  for the exact response shape
- Product intent is at https://ysur-app-doc.vercel.app/

---

## 12. Contact & Ownership

- **Project Owner**: Yasser (Era Group, Odoo developer / technical implementer)
- **Organization**: Era Group — https://era.net.sa
- **Backend repo**: `Era-Group/era_yusr_api`
- **Mobile repo**: `Era-Group/yusr-mobile` (to be created)
- **Languages**: Arabic (Egyptian dialect for informal discussion),
  English (for code, commits, and API). Communication preference is direct
  and business-oriented.

---

*Last updated: 2026-04-19*
*This file should be updated whenever major architectural decisions change.*

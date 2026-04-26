# Yusr API — Mobile Integration Guide


## Code for mobile app (changable)
```
/opt/odoo/submodules/rork-yusr-hr-self-service_latest
```

## 1. Base URL

```
https://demo.yusrhr.com
```

All endpoints are under `/api/yusr/…`. HTTPS only. CORS is open (`*`).

## 2. Prerequisites (HR must do this per employee, once)

Before a user can log in, HR opens the employee record in Odoo and sets:

| Field | Where | Value |
|---|---|---|
| **Employee Login ID** | Yusr Mobile App tab | e.g. `EMP1023` — unique, 3–32 chars, `A–Z / 0–9 / _ / -` |
| **Yusr Access Enabled** | Yusr Mobile App tab | ✓ checked |
| **PIN** | Employee header (standard Odoo PIN) or *Action → Set Yusr PIN* wizard | 4–6 digits |

## 3. Response envelope

Every response is JSON. Success:
```json
{ "success": true, "data": { … } }
```
Error:
```json
{ "success": false, "error": "message", "code": "ERROR_CODE" }
```
HTTP status mirrors success/failure (200, 400, 401, 403, 404, 409, 500).

## 4. Login

**`POST /api/yusr/auth/login`** (public)

Request:
```json
{ "employee_id": "EMP1023", "pin": "1234" }
```
Response (200):
```json
{
  "success": true,
  "data": {
    "token":          "eyJ…",
    "refresh_token":  "eyJ…",
    "expires_in":     28800,
    "token_type":     "Bearer",
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

- Access token TTL: **8 hours**
- Refresh token TTL: **30 days**
- Errors: `401 INVALID_CREDENTIALS` (wrong PIN, locked, disabled, or unknown login_id)
- Lockout: 5 wrong PINs → 15-minute lock

## 5. Using the access token

Every authenticated request sends:
```
Authorization: Bearer <token>
```
On `401 UNAUTHORIZED` or `401 NO_TOKEN`, try refresh once; if refresh also fails, send the user back to login.

## 6. Refresh

**`POST /api/yusr/auth/refresh`** (public)
```json
{ "refresh_token": "eyJ…" }
```
Returns new `token` + `refresh_token`. Replace both in secure storage.

## 7. Get the full employee profile

**`GET /api/yusr/profile`** (auth required)

Response `data`:
```json
{
  "id": 42,
  "name": "Ahmed Ali",
  "login_id": "EMP1023",
  "job_title": "Accountant",
  "department": "Finance",
  "manager": "Sara Ahmed",
  "company": "Era Group",
  "work_email": "ahmed@era.net.sa",
  "work_phone": "+9661…",
  "mobile_phone": "+96650…",
  "contract_start_date": "2023-04-01",
  "identification_id": "1234567890",
  "avatar_url": "/web/image/hr.employee/42/image_256"
}
```

Use this as the authoritative source for "show my info" screens.

To display the avatar, prefix with the base URL:
`https://demo.yusrhr.com/web/image/hr.employee/42/image_256`

## 8. Update-profile request (not a direct write)

**`PUT /api/yusr/profile`** (auth required)

Body — only these keys are accepted, everything else is ignored:
```
mobile_phone, work_phone, private_phone,
emergency_contact, emergency_phone,
private_street, private_city, private_zip
```
This does **not** write to the employee; it posts a chatter note for HR to review and apply.

## 9. Other endpoints (for later)

| Area | Method & path |
|---|---|
| Attendance | `POST /api/yusr/attendance/checkin` `{latitude, longitude, override_reason?}`, `POST /api/yusr/attendance/checkout`, `GET /api/yusr/attendance/status`, `GET /api/yusr/attendance/records?month=YYYY-MM` |
| Leaves | `GET /api/yusr/leaves/balances`, `GET /api/yusr/leaves/types`, `GET /api/yusr/leaves/requests`, `POST /api/yusr/leaves/requests`, `POST /api/yusr/leaves/requests/<id>/cancel` |
| Payslips | `GET /api/yusr/payslips`, `GET /api/yusr/payslips/<id>`, `GET /api/yusr/payslips/<id>/pdf` |
| Expenses | `GET /api/yusr/expenses`, `GET /api/yusr/expenses/categories`, `POST /api/yusr/expenses` |
| Schedule | `GET /api/yusr/schedule` |
| Calendar | `GET /api/yusr/calendar?month=YYYY-MM` |
| HR chat | `GET /api/yusr/hr/requests`, `POST /api/yusr/hr/requests` |
| Push device | `POST /api/yusr/device/register`, `DELETE /api/yusr/device/unregister` |
| Forgot PIN | `POST /api/yusr/auth/forgot-pin` `{employee_id}` → notifies HR, always returns success |
| Logout | `POST /api/yusr/auth/logout` (client-side; just discard tokens) |

## 10. Smoke test with curl

```bash
# 1. Login
curl -sS -X POST https://demo.yusrhr.com/api/yusr/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"employee_id":"EMP1023","pin":"1234"}'

# Copy "token" from the response, then:
TOKEN="eyJ…"

# 2. Fetch profile
curl -sS https://demo.yusrhr.com/api/yusr/profile \
  -H "Authorization: Bearer $TOKEN"
```

## 11. Things mobile dev needs to know

1. **Tokens stored securely**: iOS Keychain / Android Keystore (`expo-secure-store` if Expo). **Never AsyncStorage.**
2. **Refresh-on-401 interceptor**: single retry, then logout.
3. **JWT secret rotates on every backend redeploy** — on the next backend upgrade, all tokens become invalid. Design the app to handle forced re-login gracefully.
4. **Login ID is case-insensitive** on lookup but trim whitespace client-side before sending.
5. **PIN is the same one used by the Attendance Kiosk** (standard Odoo `hr.employee.pin`). Changing it via HR changes it for both.
6. **Profile edits are requests, not writes** — show a "submitted for HR approval" confirmation after PUT `/profile`.
7. **CORS is `*`** so web debugging works, but the mobile app should only hit the configured base URL.

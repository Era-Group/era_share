# Era Yusr API

REST API backend module for the **Yusr** HR mobile application (Era Group).

Provides a JWT-authenticated REST layer on top of Odoo 19, with a dedicated
**Employee ID + PIN** login flow — separate from Odoo's portal/internal user login.

---

## Features

- **Employee ID + PIN authentication** (no portal/internal user login)
- **JWT access + refresh tokens** (8 h / 30 d)
- **PIN stored on the standard `hr.employee.pin` field** (shared with the
  Attendance Kiosk), verified with `hmac.compare_digest`
- **Lockout after 5 failed attempts** (15 min)
- **Geofenced attendance check-in** (Haversine distance)
- **Push notification device registry** (`yusr.device`)
- **CORS-ready** — all endpoints support OPTIONS preflight
- 20+ endpoints covering profile, attendance, leaves, payslips,
  expenses, schedule, calendar, HR requests

---

## Installation

### 1. Dependencies

```bash
pip install PyJWT
```

Required Odoo modules: `hr`, `hr_attendance`, `hr_holidays`, `hr_expense`, `mail`, `calendar`, `resource`.
Optional: `hr_payroll` (Enterprise) for payslip endpoints.

### 2. Install the module

```bash
# Copy the module into your addons path
cp -r era_yusr_api /path/to/odoo/addons/

# Update apps list and install
./odoo-bin -c odoo.conf -d YOUR_DB -u era_yusr_api --stop-after-init
```

Or via the UI: **Apps → Update Apps List → search "Yusr" → Install**.

### 3. Configure

Go to **Settings → Yusr Mobile API**:

1. Click **Generate Strong Secret** for the JWT secret (or set your own).
2. Enable **Enforce Geofencing** and set radius (default 200 m).
3. Make sure the company partner has `partner_latitude` and `partner_longitude` set.

### 4. Set up employees

On each `hr.employee` record:

1. Open the **Yusr Mobile App** tab (requires HR group).
2. Fill **Employee Login ID** (e.g., `EMP1023`).
3. Enable **Yusr Access Enabled**.
4. Right-click the employee or use the action menu → **Set Yusr PIN**.

---

## API Endpoints

Base URL: `https://your-odoo-host`

### Authentication

| Method | Path                          | Auth       | Description                      |
|--------|-------------------------------|------------|----------------------------------|
| POST   | `/api/yusr/auth/login`        | Public     | Login with Employee ID + PIN     |
| POST   | `/api/yusr/auth/refresh`      | Public     | Exchange refresh token           |
| POST   | `/api/yusr/auth/logout`       | Bearer JWT | Logout (client-side)             |
| POST   | `/api/yusr/auth/forgot-pin`   | Public     | Notify HR of PIN reset request   |

### Profile

| Method | Path                   | Auth       | Description              |
|--------|------------------------|------------|--------------------------|
| GET    | `/api/yusr/profile`    | Bearer JWT | Get profile              |
| PUT    | `/api/yusr/profile`    | Bearer JWT | Request profile update   |

### Attendance

| Method | Path                                | Auth       | Description                        |
|--------|-------------------------------------|------------|------------------------------------|
| POST   | `/api/yusr/attendance/checkin`      | Bearer JWT | Check in (with GPS + geofence)     |
| POST   | `/api/yusr/attendance/checkout`     | Bearer JWT | Check out                          |
| GET    | `/api/yusr/attendance/status`       | Bearer JWT | Current status + today's totals    |
| GET    | `/api/yusr/attendance/records`      | Bearer JWT | Month records (`?month=YYYY-MM`)   |

### Leaves

| Method | Path                                               | Auth       | Description            |
|--------|----------------------------------------------------|------------|------------------------|
| GET    | `/api/yusr/leaves/balances`                        | Bearer JWT | Leave balances         |
| GET    | `/api/yusr/leaves/types`                           | Bearer JWT | Available leave types  |
| GET    | `/api/yusr/leaves/requests`                        | Bearer JWT | List my requests       |
| POST   | `/api/yusr/leaves/requests`                        | Bearer JWT | Submit new request     |
| POST   | `/api/yusr/leaves/requests/<id>/cancel`            | Bearer JWT | Cancel pending request |

### Payslips

| Method | Path                                | Auth       | Description        |
|--------|-------------------------------------|------------|--------------------|
| GET    | `/api/yusr/payslips`                | Bearer JWT | List payslips      |
| GET    | `/api/yusr/payslips/<id>`           | Bearer JWT | Payslip detail     |
| GET    | `/api/yusr/payslips/<id>/pdf`       | Bearer JWT | Download PDF       |

### Expenses

| Method | Path                            | Auth       | Description          |
|--------|---------------------------------|------------|----------------------|
| GET    | `/api/yusr/expenses`            | Bearer JWT | List expenses        |
| GET    | `/api/yusr/expenses/categories` | Bearer JWT | Expense categories   |
| POST   | `/api/yusr/expenses`            | Bearer JWT | Submit new expense   |

### Schedule & Calendar

| Method | Path                  | Auth       | Description                    |
|--------|-----------------------|------------|--------------------------------|
| GET    | `/api/yusr/schedule`  | Bearer JWT | Weekly schedule                |
| GET    | `/api/yusr/calendar`  | Bearer JWT | Monthly unified calendar       |

### HR Communication

| Method | Path                       | Auth       | Description            |
|--------|----------------------------|------------|------------------------|
| GET    | `/api/yusr/hr/requests`    | Bearer JWT | List messages          |
| POST   | `/api/yusr/hr/requests`    | Bearer JWT | Submit new request     |

### Device (Push)

| Method | Path                            | Auth       | Description              |
|--------|---------------------------------|------------|--------------------------|
| POST   | `/api/yusr/device/register`     | Bearer JWT | Register push token      |
| DELETE | `/api/yusr/device/unregister`   | Bearer JWT | Unregister push token    |

---

## Sample Login Flow

```bash
curl -X POST https://erp.era.net.sa/api/yusr/auth/login \
  -H "Content-Type: application/json" \
  -d '{"employee_id": "EMP1023", "pin": "1234"}'
```

Response:

```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOi...",
    "refresh_token": "eyJhbGciOi...",
    "expires_in": 28800,
    "token_type": "Bearer",
    "employee": {
      "id": 42,
      "name": "Ahmed Ali",
      "login_id": "EMP1023",
      "job_title": "Accountant",
      "department": "Finance",
      "avatar_url": "/web/image/hr.employee/42/image_128",
      "roles": ["employee"]
    }
  }
}
```

Then include the token in subsequent requests:

```bash
curl https://erp.era.net.sa/api/yusr/profile \
  -H "Authorization: Bearer eyJhbGciOi..."
```

---

## Security Notes

- Always serve over **HTTPS** in production.
- Rotate `era_yusr_api.jwt_secret` periodically — this invalidates all active sessions.
- PINs are stored on the stock `hr.employee.pin` field (HR-restricted) and
  verified with `hmac.compare_digest` to avoid timing leaks. Plain PINs are
  never logged. The legacy `pin_hash` column was dropped in v19.0.2.0.0.
- Accounts lock for **15 minutes** after **5 consecutive failed attempts**.
- Consider adding rate limiting at the reverse proxy layer (nginx/Cloudflare).

---

## Module Structure

```
era_yusr_api/
├── __init__.py
├── __manifest__.py
├── controllers/           REST API controllers
│   ├── base.py            JWT decorator, JSON helpers, CORS
│   ├── auth.py
│   ├── profile.py
│   ├── attendance.py
│   ├── leaves.py
│   ├── payslips.py
│   ├── expenses.py
│   ├── schedule.py
│   ├── calendar_ctrl.py
│   ├── hr_requests.py
│   └── device.py
├── models/
│   ├── hr_employee.py     Adds employee_login_id + lockout fields;
│   │                      PIN reuses the stock hr.employee.pin field
│   ├── res_config_settings.py
│   └── yusr_device.py
├── wizard/
│   └── set_pin_wizard.py  HR wizard to set/reset an employee's PIN
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

---

## License

LGPL-3.0

## Author

Era Group — <https://era.net.sa>

# era_address_client — Saudi National Address Client (Odoo 19)

Odoo 19 module. Adds a "تحويل العنوان المختصر" button to the partner
form that calls the remote `era_address_lookup` service hosted at
`service.era.net.sa` and auto-fills address fields.

---

## Setup

### 1 — Install on the Odoo 19 instance

Drop `era_address_client/` into your addons path and install the module.

### 2 — Configure the server connection

Go to **Settings → General Settings → خدمات تحويل العنوان** and fill in:

| Setting | Value |
|---|---|
| **عنوان خادم تحويل العنوان** | `https://service.era.net.sa` (default) |
| **رمز API للخادم** | The token set on the server in `era_address_lookup.api_token` |

---

## How It Works

```
User enters x_short_address (e.g. JCHA4298)
          │
          ▼
[click "تحويل العنوان المختصر"]
          │
          ▼
POST https://service.era.net.sa/api/era/address/lookup
     { short_address: "JCHA4298", token: "<secret>" }
          │
          ▼
  Server resolves via HERE + Google
          │
          ▼
  Response: { building_number, street, district,
              city, postal_code, additional_number, country }
          │
          ▼
  Written to res.partner fields
```

---

## Partner Fields Added

| Field | Label |
|---|---|
| `x_short_address` | العنوان المختصر |
| `x_building_number` | رقم المبنى |
| `x_additional_number` | الرقم الإضافي |
| `x_district` | الحي |

Standard fields also updated: `street`, `city`, `zip`, `country_id`.

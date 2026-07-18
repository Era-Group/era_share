# Era Customer Success (`era_customer_success`)

Customer Success platform for Odoo 19 Enterprise — a thin orchestration layer that
gives every customer a **Customer Success Account** assigned to a CSM engineer, a
unified 360° timeline of every touch-point (Helpdesk tickets, VoIP calls, WhatsApp,
SMS, email, surveys, subscriptions, collections), an automatic **health score**, a
**30-60-90 lifecycle**, and a **manager KPI dashboard** to evaluate performance and
follow-up level.

Reuses (does not reinvent): `helpdesk`, `voip` (+ `era_voip_ext` AI), `whatsapp`
(native), `sale_subscription`, `account_followup`, `survey`,
`crm`, `mail.activity.plan`.

## Documentation (Arabic)
- `doc/FEATURES_AR.md` — full feature reference.
- `doc/USER_GUIDE_AR.md` — usage guide for manager + engineer.
- `doc/IMPROVEMENTS_AR.md` — improvement roadmap (AI sentiment, etc.).

## Key models
`cs.account` · `cs.stage` · `csm.offering` · `csm.kpi.snapshot` · `cs.capture.request`

## Security groups
Customer Success Engineer (CSM) — sees own accounts · Customer Success Manager — sees all.

Validated on Odoo 19.0 EE via fresh install + upgrade + runtime smoke test.

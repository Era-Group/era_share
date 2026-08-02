# era_email_verification — Deployment, Testing & Development

How to install the addon on the Odoo server, wire the result-push webhook to the
verifier, test it end-to-end, and keep developing. Companion doc for the
verifier side: `DEPLOYMENT.md` in the **email_verifier_service** repo.

---

## 1. The two moving parts

| Component | Where | Talks to |
|---|---|---|
| **Verifier API** | `95.216.168.230`, `https://email-validation.letsw.com` | receives jobs; PUSHES results back |
| **This addon** (Odoo 19) | the Odoo server (separate host) | submits jobs; RECEIVES pushes at `/era_email_verification/webhook` |

Flow: Odoo submits a job (with a callback URL + per-batch secret) → the verifier
checks the emails → it **pushes** completed results to Odoo's webhook (HMAC
signed) → a poll reconciles as a fallback. One job is in flight at a time.

---

## 2. Install on the Odoo server

```bash
# on the Odoo host, in the crm19 addons checkout
cd /path/to/crm19
git pull origin main            # era_email_verification is on main

# make sure crm19 is on the addons_path (odoo.conf) then:
odoo-bin -c /etc/odoo/odoo.conf -d <DB> -u era_email_verification --stop-after-init
# first install: use -i instead of -u
```
Or from the UI: **Apps → Update Apps List → search "ERA Email Verification" →
Install**. Depends on `mail`, `contacts`, `mass_mailing` (all standard).

Grant users the groups **Email Verification / User** (request checks, see
statuses) or **/ Manager** (configure, bulk, SMTP details).

---

## 3. Configure — Settings ▸ Email Verification

| Setting | Value |
|---|---|
| **Verifier Base URL** | `https://email-validation.letsw.com` |
| **Verifier API Key** | the `EMAIL_VERIFIER_API_KEY` from the verifier's `.env` |
| Verify TLS | on |
| **Push results (webhook)** | on (leave off for poll-only) |
| **Public Odoo URL for push** | a public **HTTPS** URL for THIS Odoo the verifier can reach; blank → uses `web.base.url` |
| Fallback poll after (min) | 5 (keep below the scheduled action's 15-min interval) |
| Blacklist these outcomes | *Undeliverable* + *Risky* (default). *Disposable*, *Unknown* and *Catch-all* are separate boxes, off by default |
| Eligibility threshold / Auto re-check | as desired |

Click **Test connection** to confirm the Base URL + key. Secrets are stored in
`ir.config_parameter` (system admins only) and never logged.

### Networking the webhook needs (both directions)
- **Odoo → Verifier**: outbound HTTPS to `email-validation.letsw.com`.
- **Verifier → Odoo**: inbound HTTPS to the *Public Odoo URL* above, path
  `/era_email_verification/webhook`. If the verifier can't reach Odoo, **nothing
  breaks** — results still import via the fallback poll (just up to ~15 min later).
- The verifier refuses non-HTTPS and private/internal callback hosts (SSRF).

---

## 4. Test end-to-end

### On the local Odoo 19 dev environment (`~/odoo19`, already set up)
```bash
cd ~/odoo19
./venv/bin/python odoo/odoo-bin -c odoo.conf -d evtest \
  -u era_email_verification --test-enable --test-tags=/era_email_verification \
  --stop-after-init --log-level=test
# expect: "era_email_verification: NN tests ... 0 failed, 0 error(s)"
```
The network seam is mocked, so tests are offline and safe. The suite covers
state mapping, the batch flow, the email-change race, pagination/idempotency,
serialization, the incremental fallback, and the webhook controller
(HMAC / replay / unknown-job) via `HttpCase`.

### Live smoke test on the Odoo server
1. Pick a few contacts with emails → list **Action → Verify Email(s)**, or run
   **Email Verification ▸ Operations ▸ Verify Unchecked Emails**.
2. Watch **Operations ▸ Batches**: `queued → running → done`, with live counts.
3. Confirm push is arriving (not just the fallback): on the verifier,
   `docker compose logs -f` shows callback POSTs; in Odoo the batch's
   *Last result push* updates within seconds. Risky/undeliverable addresses
   appear under **mail.blacklist** per policy.
4. If pushes never arrive, results still complete via the poll — check the
   *Public Odoo URL*, TLS, and that the verifier host can reach it.

---

## 5. Development workflow

```bash
# 1. branch off main
git checkout -b feature/<change>
# 2. edit the addon; run the test suite locally (see §4) until green
# 3. commit & push, open a PR to main
git commit -am "..." && git push -u origin feature/<change>
# 4. after merge to main: on the Odoo server
git pull origin main
odoo-bin -c odoo.conf -d <DB> -u era_email_verification --stop-after-init
```
Restart the Odoo service after `-u` if running as a daemon. Batches are
submitted immediately when queued (and the next one is chained as each
finishes) via `ir.cron._trigger()`. The scheduled action
**"Email Verification: reconcile & sweep (fallback)"** (every 15 min) is the
safety net — it reconciles what a push missed, retires stuck batches, purges
remote jobs and runs the re-check sweep. Make sure it's active.

---

## 6. How the pieces map (for the next developer)

| File | Role |
|---|---|
| `models/verifier_client.py` | the ONE outbound seam (Bearer auth, SSRF-safe) |
| `models/email_verification_batch.py` | batch lifecycle, submit/serialize, push apply, fallback pull, cron |
| `models/email_verification_item.py` | one address + its imported raw result |
| `models/res_partner.py` / `mailing_contact.py` | verification fields, email-change reset, actions |
| `models/constants.py` | statuses, `should_blacklist`, `is_eligible` |
| `controllers/main.py` | public webhook: HMAC verify + replay guard + idempotent apply |
| `models/res_config_settings.py` + `views/res_config_settings_views.xml` | Settings UI |
| `tests/` | 45 unit + HttpCase tests (mock the verifier) |

Webhook contract (must match the verifier): signature =
`sha256=HMAC_SHA256(batch.callback_secret, "<X-EV-Timestamp>." + raw_body)`,
timestamp within 300 s; every auth failure returns a uniform `401`.

---

## 7. Security & safety

- API key + per-batch push secret live in `ir.config_parameter`, system-only,
  never returned or logged.
- The public webhook only ever attaches results to the one batch named by
  `job_id`, guarded by the per-batch HMAC secret; any auth failure → 401.
- Multi-company record rules isolate batches/items; users see only their own,
  managers see all.
- Contacts/emails are never deleted or cleared automatically — only statuses are
  set and `mail.blacklist` entries added (per policy). Verification is a
  technical check, not proof of consent or engagement.

---

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| Batches stuck `queued` | is the "reconcile &amp; sweep (fallback)" cron active? is another batch `running` (only one runs at a time)? |
| Batches stuck `running`, no results | verifier reachable? API key correct? see the batch's *Error*; fallback pulls after 15 min |
| Pushes never arrive (only fallback) | *Public Odoo URL* set to a reachable HTTPS? verifier→Odoo firewall? verifier logs for callback POST errors |
| `Test connection` fails | Base URL (https) + API key; verifier `/health` up |
| Nothing blacklisted | policy = *Off*? or all deliverable? catch-all is excluded by default |

# ERA Email Verification

Integrates Odoo 19 with the self-hosted **Email Verifier API** to verify
contact (`res.partner`) and mailing-list (`mailing.contact`) email addresses,
show the result on the contact, and automatically blacklist bad addresses.

No third-party provider is contacted and no SMTP probing happens inside an Odoo
worker — everything goes through the single, administrator-configured verifier.

## What it does

- **Verify on the contact form** — a status badge sits next to the email, with
  a *Verify* button for an immediate check and an *Email Verification* tab
  showing score, reason, catch-all/disposable/role signals and last-checked date.
- **Bulk & sweep** — *Verify Email(s)* on the Contacts and Mailing Contacts list
  actions, plus **Verify Unchecked Emails** (Email Verification ▸ Operations)
  which queues every never-checked address in the background.
- **Asynchronous batches** — jobs (≤ 5,000 addresses each) are submitted the
  moment they are queued and chained as each one finishes, via
  `ir.cron._trigger()`. Results import in bounded, idempotent, crash-safe
  steps, then the remote job is deleted. The scheduled action itself runs
  every 15 minutes purely as a **fallback**: it reconciles anything the push
  missed, retires stuck batches, and runs the optional re-check sweep.
- **Auto-blacklist** — bad addresses are added to `mail.blacklist` per a
  **configurable policy** (Settings): one independent checkbox per outcome —
  Undeliverable, Risky, Disposable, Unknown, Catch-all. Ship defaults are
  Undeliverable + Risky; unticking everything disables auto-blacklisting.
  Each box is explained in the settings screen.
- **Periodic re-check** — with *Auto re-check* on, the sweep also re-queues
  mailing-eligible addresses whose last check is older than *Re-check after
  (days)*. A **Stale (needs re-check)** filter on Contacts and Mailing
  Contacts shows exactly who that is.
- **Safety** — changing a contact's email resets its status and re-queues it; a
  result is never written onto an email that changed after the batch was sent.
  A per-address failure is recorded on that item alone and never aborts the
  rest of the import, and a running batch that stops making progress for
  *Give up on a stuck batch after (h)* is failed so it cannot block the queue.

## Configuration

Settings ▸ **Email Verification** (Manager) / the *Connection* block (Admin):

| Setting | Notes |
|---|---|
| Verifier Base URL | e.g. `https://email-validation.letsw.com` (HTTPS; `http` only for localhost) |
| Verifier API Key | `Authorization: Bearer …`; admin-only, never logged |
| Verify TLS | keep on in production |
| Batch size | ≤ 5,000 |
| Probe SMTP / Detect catch-all | verification defaults |
| Eligibility threshold | min score for *Mailing-eligible* (default 80) |
| Auto re-check + stale days | opt-in periodic re-check of never-checked and aged addresses |
| Give up on a stuck batch after (h) | fail a running batch that imports nothing for this long (default 6) |
| **Auto-blacklist outcomes** | a checkbox per outcome; see the in-screen explanation |

Use **Test connection** to confirm the base URL and key.

## Security

- Two groups: *Email Verification: User* (request checks, view statuses) and
  *Manager* (configure, run bulk, view SMTP details).
- Users see only their own batches/items; managers see all. Multi-company
  isolation via global record rules.
- The API key lives in `ir.config_parameter` (system admins only), is never
  logged or returned, and only the one configured HTTPS host is ever contacted
  (redirects refused → SSRF-safe).

## Deployment checklist

1. Copy `era_email_verification` into the addons path and
   `-u era_email_verification` (or install from Apps).
2. Settings ▸ Email Verification: set Base URL + API Key, click *Test
   connection*.
3. Tick the **auto-blacklist outcomes** (default: Undeliverable + Risky).
4. Grant users the *Email Verification* groups.
5. Confirm the cron **"Email Verification: reconcile & sweep (fallback)"** is
   active (every 15 min; batches themselves start immediately, not on this tick).
6. Run **Verify Unchecked Emails** once to seed statuses, then watch
   Operations ▸ Batches.

## Tests

`odoo -d <db> -i era_email_verification --test-enable --stop-after-init`
covers state mapping, the batch flow, the email-change race, pagination,
idempotent submit/re-import, the blacklist policy matrix, client SSRF/secret
guards, record-rule access isolation, and the robustness paths (unparseable
addresses, per-item error isolation, the auto re-check pile-up guard, stale
selection, the stuck-batch valve, and remote-job cleanup on
cancel/fail/reset). The network seam is mocked.

## Notes / limitations

Verification reflects a technical check at a point in time; it does not prove
consent, engagement, ownership, or inbox placement. Contacts and emails are
never deleted or cleared automatically — only statuses are set and blacklist
entries added.

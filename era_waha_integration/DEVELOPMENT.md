# era_waha_integration — Developer Guide

Portable knowledge base for continuing development of this module in any
environment. (Odoo 19 Enterprise.)

## 1. Purpose

Plug the **WAHA** WhatsApp HTTP API (https://waha.devlike.pro — an unofficial
WhatsApp-Web gateway, NOWEB engine) into Odoo's standard Enterprise
**`whatsapp` + Discuss** stack as an alternative backend *provider*, so WAHA
conversations behave like official WhatsApp inside Discuss.

The pre-existing `sadeem_waha_whatsapp` module (in
`submodules/waha-whatsapp_latest/`, a protected vendor dir) talks to WAHA too but
works **separately from Discuss**; this module is the Discuss-native replacement.
They can coexist (distinct webhook route + models).

## 2. Architecture decision

**Reuse `ee/whatsapp` entirely; only swap the send/receive backend to WAHA.**
- `whatsapp.account` gains `provider = 'meta' | 'waha'`.
- WAHA channels reuse `channel_type='whatsapp'` → the composer, read-ticks,
  partner matching, channel creation and all Discuss JS come for free.
- Outbound: `whatsapp.message._send_message` routes `provider=='waha'` messages
  to `_waha_send_one`; Meta messages fall through to `super()`.
- Inbound: a controller feeds WAHA webhook events into the standard
  `discuss.channel._get_whatsapp_channel_from_identifiers` + `message_post`.

Do **NOT** edit `/opt/odoo/ce`, `/opt/odoo/ee`, or
`/opt/odoo/submodules/waha-whatsapp_latest` (upstream/vendor). This module lives in
`/opt/odoo/submodules/era_share_latest/era_waha_integration` (shared production
layer, repo `eranetsa/era_share`, branch `19.0`) — **relocated there from
`/opt/odoo/addons` on 2026-07-19** (see §8). `addons/` precedes `era_share_latest`
in the addons_path, so the module resolves from era_share only because it is no
longer in `addons/`; `get_module_path('era_waha_integration')` confirms the path.

## 3. File map

```
models/
  whatsapp_account.py   whatsapp.account: provider + WAHA fields; HTTP helper
                        (_waha_request); session/QR actions; inbound
                        (_waha_process_incoming/_ack/_reaction/_session_status);
                        outbound send helpers; history backfill; out-of-band
                        status writes (_waha_update); id extraction/matching.
  whatsapp_message.py   whatsapp.message: _send_message split, _waha_send_one,
                        _resend_failed, _get_whatsapp_gc_domain overrides; the
                        html_to_whatsapp() rich-HTML→WhatsApp-formatting converter.
  discuss_channel.py    discuss.channel: empty 24h window for WAHA; is_waha_channel
                        flag to store; whatsapp_outbound_msg_uid notify hook;
                        action_waha_import_history; get_whatsapp_channel_for_record.
  mail_message.py       mail.message: _message_reaction routed to WAHA (not Meta).
  res_users_settings.py is_discuss_sidebar_category_waha_open.
  res_users.py          _init_store_data adds global wahaCategoryName (account name).
  discuss_channel_member.py  _mark_as_read → _waha_mark_seen (sendSeen read receipts).
  waha_exceptions.py    WahaSendLimit(UserError), WahaNewNumberLimit(WahaSendLimit).
controllers/waha_webhook.py   POST /era/waha/webhook (message/message.ack/
                              message.reaction/session.status), optional HMAC.
wizard/whatsapp_composer.py   whatsapp.composer: wa_account_id picker + WAHA
                              rich-text/image mode (waha_body is an Html editor);
                              routes send through the Discuss channel.
wizard/mail_compose_message.py  mail.compose.message: "WhatsApp" button + WAHA
                              account picker in the general Send-Message dialog;
                              strips the email signature (_waha_strip_signature),
                              routes through the channel. Shared send entrypoint:
                              whatsapp.account._waha_send_via_channel.
static/src/composer/waha_composer.scss   enlarge the composer message box.
views/whatsapp_account_views.xml     WAHA fields, session/QR buttons, hides Meta bits.
views/whatsapp_composer_views.xml    account picker + WAHA body/attachment.
data/ir_cron_data.xml                5-min status refresh cron; daily reconcile cron.
data/ir_actions_server_data.xml      "Import previous conversation" server action.
static/src/core/public_web/*.js      separate "WAHA" Discuss sidebar category
                                     (store_service/thread_model/discuss_app patches).
static/src/core/common/message_patch.js/.xml   WhatsApp-style ✓/✓✓/blue ticks +
                                     failed-post tooltip reason (patches mail.Message).
static/src/core/common/message_model_patch.js  wahaBlockReason field on the message.
static/src/core/common/message_post_reason_patch.js  Store.doMessagePost override:
                                     surface the WAHA block reason (toast) on send fail.
static/src/float/*                   floating Discuss window + systray + chatter
                                     button, with All/WAHA/Standard list toggle.
security/waha_security.xml           ir.rule: Default Users read their account's msgs.
migrations/19.0.1.7.0/post-migrate.py  backfill Default-User channel membership.
i18n/ar.po                           full Arabic translation (applies to ar_001).
```

## 4. Features implemented

- Two-way text + media (image/file/voice) between WAHA and Discuss.
- Partner matched/created by phone number; channel per number.
- Delivery/read ticks from `message.ack`.
- Inbound customer reactions → Discuss (`message.reaction`).
- Session management + QR + auto webhook registration on the account form.
- Smart history backfill (last-2-message check; import gap; idempotent).
- Separate **WAHA** sidebar category (distinct from Meta WhatsApp).
- Floating WhatsApp window (systray launcher) with All/WAHA/Standard toggle.
- "Send WhatsApp" composer: account picker; WAHA rich-text/image without a
  template; the send appears in the Discuss channel + a note on the record.
- The general "Send Message" (mail.compose.message) dialog gets a WhatsApp button
  + WAHA account picker; the composed message (e.g. AI-drafted) is sent over WAHA
  with the email signature removed.
- Rich text (bold/italic/strike/monospace/bullets/numbers) is converted to
  WhatsApp formatting (`*`, `_`, `~`, ```` ``` ````, `•`, `1.`) on send.
- **Account-protection (anti-ban) guards** on every outbound path
  (`whatsapp.account._waha_check_send_allowed`): Rule A — a never-replied contact
  is messageable once per `waha_cold_resend_hours` (24h); Rule B — per-user daily
  brand-new-number cap `waha_new_number_daily_limit` (5), then composers redirect
  to the official Meta composer; Rule C — global balance
  `outbound <= waha_balance_base(25) + ratio*inbound` over a window, ratio ages up
  weekly with account age (`waha_working_since`, `_cron_waha_age_adjust`). Blocks
  raise `WahaSendLimit` (request rolls back → nothing shows as failed). The guard
  is FAIL-OPEN. Rationale: WAHA use violates Meta ToS per se and enforcement is
  automated (deep research), so keep business-initiated outreach on the official
  Cloud API and use WAHA for inbound-driven support. Limits configurable on the
  account form.

## 5. WAHA API (base `{server_url}/api/`, header `X-Api-Key`)

| Purpose | Method | Path | Body |
|---|---|---|---|
| Send text | POST | `sendText` | `{chatId:"<digits>@c.us", text, session}` |
| Send image/file/voice | POST | `sendImage`/`sendFile`/`sendVoice` | `{chatId, session, file:{mimetype,filename,data(b64)}, caption?}` |
| Start session | POST | `sessions` | `{name, start:true, config:{noweb:{store:{enabled,full_sync}}, webhooks:[{url,events,hmac?}]}}` (422→PUT) |
| Status | GET | `sessions/{s}` | → `{status, me:{id,pushName}}` |
| QR | GET | `screenshot?session={s}` | → PNG bytes |
| React | PUT | `reaction` | `{session, chatId, messageId, reaction}` |
| History | GET | `{s}/chats/{chatId}/messages?limit&offset&downloadMedia=true` | newest-first |

Webhook envelope: `{event, session, payload}`. We subscribe to
`message, message.ack, message.reaction, session.status`. Inbound payload:
`from, fromMe, body, id, type, hasMedia, media{url,mimetype,filename}, notifyName,
timestamp, reaction{messageId,text}`. `@lid` JIDs resolve via `GET {s}/lids/{lid}`
→ `pn`.

## 6. Critical implementation notes (do not regress)

- **Message id matching:** inbound `msg_uid` is WAHA's full serialized id
  (`fromMe_remoteJid_HASH`); **outbound** `msg_uid` comes from the send response as
  the **bare HASH**. Acks/reactions reference the full serialized id, so
  `_waha_find_message` matches the full id then falls back to the trailing hash
  segment (`uid.rsplit('_',1)[-1]`). Without this, ticks stick at 'sent' and
  reactions on our messages are dropped. **`_waha_uid_exists` (the idempotency/known
  check used by dedup, the history import and the reconcile) MUST use the same
  full-id-OR-trailing-hash match** — the chat/overview APIs return the full
  `fromMe_remoteJid_HASH` (often `@c.us`), but we store outbound as the bare HASH and
  inbound under its `@lid` id, so an exact-only match reads them as unknown and the
  reconcile re-imports **duplicates**. The HASH is the globally-unique key.
- **Gap recovery after a disconnection (reconcile):** `_waha_reconcile_overview` scans
  WAHA's `chats/overview` (most-recently-active chats, each with its last message),
  and for every 1:1 chat within `WAHA_RECONCILE_WINDOW_HOURS` (72h) whose newest
  message is unknown, ensures a channel exists (so **numbers that first messaged during
  the outage — with no channel yet — are discovered**) and backfills via
  `_waha_sync_channel_history`. Idempotent. Runs every 15 min (cron) + on reconnect
  (`_waha_apply_status` `_trigger`s the cron when status goes non-working→working). The
  earlier version scanned only existing channels (missed new numbers) and ran daily.
- **Serialization failures:** Odoo requests run in REPEATABLE READ, so concurrent
  writes to the `whatsapp_account` row (session.status webhook + QR/refresh)
  abort with "could not serialize access due to concurrent update" and exhaust
  the retry loop; advisory locks do NOT help (snapshot fixed at txn start).
  `_waha_update()` writes `waha_status/phone_number/waha_qr_image` in an
  **autonomous** cursor (`with Registry(db).cursor()`, like
  `whatsapp.account._add_ir_log`) and swallows `psycopg2.Error`. Never write these
  fields via the request transaction.
- **24h window:** WAHA has none. `discuss.channel._compute_whatsapp_channel_valid_until`
  returns **False** for WAHA (NOT a far-future date — that spins composer_patch.js
  in a clamped setTimeout loop). Also override `_resend_failed` to bypass the
  validity gate for WAHA.
- **Meta assumptions overridden:** `_message_reaction` (would call Meta),
  `_resend_failed`, `_get_whatsapp_gc_domain` (keep WAHA msg_uid as durable dedup
  key).
- **Inbound webhook:** subscribe to `message` (not `message.any`) and filter
  `fromMe` to avoid echoing our own sends. Dedup with
  `pg_advisory_xact_lock(hashtext(msg_uid))` + a SAVEPOINT around `message_post`.
- **Separate category ↔ float:** WAHA channels live in `store.discuss.waha`, not
  `store.discuss.whatsapp`; the float combines both categories.
- **Composer:** `default_get` must not abort with "no template" when a WAHA
  account exists (passes `era_waha_available` context, overrides
  `_raise_no_template_error`). The WAHA send routes through the channel
  (`_get_whatsapp_channel_from_identifiers` + `message_post`), not the record log,
  so it shows in Discuss.
- **Body must be Markup, not str:** anything passed to `message_post(body=...)`
  must be a `markupsafe.Markup` (HTML). A plain `str` is escaped, and WhatsApp
  then receives literal `<div>…</div>` tags. `_waha_strip_signature` returns
  Markup for this reason.
- **Formatting:** `_waha_send_one` uses `html_to_whatsapp(self.body)` (NOT
  html2plaintext) to map rich HTML to WhatsApp markers. WhatsApp needs the markers
  adjacent to the text (`*bold*`, not `* bold *`).

## 7. Setup (runtime)

1. Install the module (pulls in `whatsapp`).
2. WhatsApp → Configuration → Accounts → new account, **Provider = WAHA**, set
   Server URL / API Key / Session; **Start Session** then **Get QR Code**; scan
   until status `working`. Start Session registers the webhook (incl.
   `message.reaction`) at `<base_url>/era/waha/webhook`.
3. Use a **dedicated** WAHA session for this integration — a session's webhook
   list is replaced on update, so don't share it with `sadeem_waha_whatsapp`.

## 8. Deployment (repo `eranetsa/era_share`, `/opt/odoo/submodules/era_share_latest`, branch `19.0`)

Since 2026-07-19 the module deploys through the **era_share submodules pipeline**,
not the old crm19/`addons` one. A `_submodules_monitor_loop` (bash, ~10s) does, for
each `/opt/odoo/submodules/*`: `git fetch` + compare local HEAD vs `origin/<branch>`;
if different → `git reset --hard <remote>` and POST a cicdoo restart trigger. The
restart runs `odoo-bin -u aidoo,era_waha_integration,sadeem_waha_whatsapp`.

- **The monitor only triggers when local HEAD differs from `origin/19.0`.** So a
  plain "commit locally then push" does NOT deploy (local == origin → no diff).
  Deploy pattern that works:
  ```
  git add -A era_waha_integration
  git commit -m "..."
  NEW=$(git rev-parse HEAD)
  git push origin $NEW:refs/heads/19.0     # advance origin
  git reset --soft HEAD~1                   # keep LOCAL behind origin, tree intact
  ```
  The next monitor cycle sees origin ahead → `reset --hard` to your commit → restart
  with `-u`. (`reset --soft` keeps your changes on disk, so the module is never
  missing during the gap.)
- **Version bump every functional change** (`__manifest__.py`) — it's the signal used
  to confirm the deploy landed (`ir_module_module.latest_version`) and to trigger
  migrations.
- **Migrations:** `migrations/<version>/post-migrate.py` with `def migrate(cr, version)`
  runs on `-u` when upgrading past `<version>`. Make them idempotent + per-record
  fail-safe (a raise aborts the whole `-u` → module rolls back → possible downtime).
- **A bad view/data file aborts the whole `-u`** (schema+views roll back, module can
  fail to load → odoo down). Pre-flight risky view changes in a rolled-back savepoint
  (`create` a transient `inherit_id` view, call `get_view`); grep the log for
  `ParseError`/`Invalid field` after deploy.
- `/var/log/odoo/odoo.log` is **truncated/rotated** around each deploy — capture what
  you need first; a missing migration log line usually means it rotated, not that the
  migration didn't run. DB name = instance UUID.
- **i18n:** after adding user-facing strings, export the POT with the exact refs
  (`odoo-bin i18n export -c odoo.conf -d <db> -o out.pot era_waha_integration`), fill
  Arabic into `i18n/ar.po`, redeploy. Active Arabic lang is `ar_001`, but a base
  `ar.po` applies (Odoo iterates `get_base_langs`). Validate with `babel.read_po`.

## 9. Testing

No browser here, so exercise the server logic via `odoo shell`, mocking only the
WAHA HTTP boundary (patch `type(env['whatsapp.account'])._waha_request`):

```
venv/bin/python3 ce/odoo-bin shell -c odoo.conf -d <uuid> --no-http < script.py
```

Cover: account create without Meta creds; inbound → channel+partner+message;
24h window empty; outbound reply → sendText + msg_uid + state; ack → read;
inbound reaction; ack/reaction match a bare-hash outbound via serialized id; GC
excludes WAHA; reaction routed to WAHA; history import (deep + idempotent +
shallow in-sync); composer WAHA send routes through a channel + note. Frontend
(sidebar category, float, composer view) needs manual browser verification.

## 10. Known limitations

- Discuss orders messages by id, not date → a *later* history backfill into an
  active channel appears at the bottom (with a separator note); auto-import on
  channel creation keeps order.
- Historical outbound (sent from the phone) shows with the operator's name.
- Live `fromMe` messages aren't captured via webhook (avoids echo); picked up by
  history import / reconciliation.
- The float + composer account picker are gated to `whatsapp.group_whatsapp_admin`
  (float) — broaden if all agents need them.

## 11. Account health monitoring & scoring (Health page)

WAHA exposes no official quality rating, so `whatsapp.account._waha_health_data()`
derives a proxy score 0-100 over a **7-day window on `mail_message_id.date`** (NOT
`create_date`, so back-filled history with old dates drops out and only live sends
count). Computed **non-stored** fields: `waha_health_score/label`,
`waha_kpi_outbound/inbound/delivered_rate/read_rate/error_rate` (rates stored as
0-100 → NO percentage widget), and an HTML `waha_health_reason` breakdown.

Score = 100 minus: `-60` if status != working; `-min(40, error%)`; a configurable
delivery penalty (below); `-min(20, flap*5)`. Label good≥80 / warning≥50 / critical.
`waha_flap_count` increments in `_waha_write_status` per session-status change, reset
daily by `_cron_waha_age_adjust`. `_cron_waha_health_check` (30 min) sends a
de-duplicated **OdooBot direct message** (not a chatter post) to `notify_user_ids` on
degrade and clears on recovery.

- **KPI FUNNEL INVARIANT (do not regress):** Delivered % and Read % MUST use the same
  denominator `sent_plus` (outbound in state sent/delivered/read = WhatsApp-accepted).
  Since `read ⊆ delivered ⊆ sent_plus`, `read_rate ≤ delivered_rate ≤ 1` always. An
  earlier bug divided Read by `delivered` (a conditional rate) → Read could exceed
  Delivered and look impossible.
- **Delivery penalty is CONFIGURABLE** (per account, Health page):
  `waha_health_deliv_warn_pct`/`_warn_penalty` (default 50%/-5) and
  `_crit_pct`/`_crit_penalty` (default 30%/-15); a tier is off when its threshold OR
  penalty is 0. Defaults are lenient **because WAHA delivery receipts (ack 2) are
  unreliable/delayed**, so a moderate confirmed-delivery rate is normal — the KPI
  stays honest, only the score judgement is relaxed. These fields are in
  `_compute_waha_health`'s `@api.depends` so the form score updates live.
- GOTCHA: field `help=` is NOT %-interpolated → use a single `%`, never `%%`.

## 12. Per-account access — "Default Users"

The account's standard `notify_user_ids` ("Default Users (access)") is reused as the
per-account access list: users who see ALL the account's conversations and can send
through it **without being WhatsApp admins**. Verified end-to-end that an INTERNAL
non-admin user added here gets full view + send.

Chain (all pieces required):
- **Read the account:** `whatsapp.account` ACL grants read to `base.group_user`.
- **Read the account's messages:** `security/waha_security.xml` ir.rule
  `waha_rule_message_member` domain `[('wa_account_id.notify_user_ids','in',user.id)]`
  (for `base.group_user`, OR-ed with EE's own-messages rule).
- **See the conversations:** channel membership is synced from `notify_user_ids` —
  `whatsapp_account.create/write` → `_waha_apply_channel_members(added, removed)`
  (adds/removes members on ALL the account's channels, sudo), plus
  `_waha_grant_members(channel)` on every new channel. `migrations/19.0.1.7.0/
  post-migrate.py` backfills membership for pre-existing Default Users on upgrade
  (the runtime sync only fires when the list CHANGES).
- **Send:** create/write on `whatsapp.message` (base.group_user ACL) + channel
  membership + the send uses `self.sudo().waha_api_key` for the API header only.
- **HARD REQUIREMENT:** Default Users must be **Internal Users** — the field domain is
  `[('share','=',False)]`, so Portal/external users can't be added AND can't read
  `whatsapp.account`. The error *"not allowed to access whatsapp.account, allowed
  groups: WhatsApp admin / Settings / Internal User"* means the person is a Portal
  user → set their User Type to Internal User (licensing cost). Do NOT widen the ACL
  to portal users.
- **View gotcha:** in the account form the field is placed DIRECTLY in the
  Connection-page `<setting>` (`notify_user_ids` appears twice — invisible in the
  hidden `tocontrol` block, visible in the WAHA notebook). An earlier
  `<xpath position="move"/>` silently failed (move nested in a `position="before"`
  insert doesn't relocate), leaving the box empty. Odoo allows a field twice in a form.

## 13. Delivery/read ticks, read receipts, typing, failed-send reason

- **WhatsApp-style ticks** (`static/src/core/common/message_patch.js/.xml`): patch the
  `mail.Message` component (NOT `whatsapp.Message`) to replace EE's green whatsapp icon
  with ✓ (sent) / ✓✓ grey (delivered) / ✓✓ blue (read) / red ! (error) for outbound.
  Live push via `Store(bus_channel=msg._bus_channel()).add(msg, {'whatsappStatus':state}).bus_send()`.
- **Read receipts to the customer:** `discuss_channel_member._mark_as_read` override
  calls `account._waha_mark_seen(channel)` (WAHA `sendSeen`) so the customer sees blue
  ticks when an agent reads.
- **Typing simulation + human pacing:** `_waha_simulate_typing(chat_id, text)` —
  startTyping, sleep `min(6, max(2, len/20))`, stopTyping — before an outbound send.
- **Failed-send reason surfacing** (`static/src/core/common/message_post_reason_patch.js`
  + `message_model_patch.js`): Discuss posts via `rpc(...,{silent:true})` and
  `Store.doMessagePost` SWALLOWS the error, so a guard block (`_waha_check_send_allowed`
  → `UserError`) showed only a generic "Failed to post" tooltip. The override, for WAHA
  channels + `err.exceptionName==='odoo.exceptions.UserError'`, shows a sticky toast
  with `err.data.message` and stores it on the message so the warning-icon tooltip
  explains why. Composer wizards surface the same reason as a clean UserError dialog
  (the guard re-raises `UserError(str(err))` because the custom `WahaSendLimit`
  subclass serializes under its own name and the web client renders it as a raw
  RPC_ERROR).

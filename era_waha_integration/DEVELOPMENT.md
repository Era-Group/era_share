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
`/opt/odoo/submodules/waha-whatsapp_latest` (upstream/vendor). All changes live
here in `/opt/odoo/addons/era_waha_integration`.

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
static/src/core/public_web/*.js      separate "WAHA" Discuss sidebar category.
static/src/float/*                   floating Discuss window + systray + chatter
                                     button, with All/WAHA/Standard list toggle.
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
  reactions on our messages are dropped.
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

## 8. Deployment (this instance: repo Era-Group/crm19, `/opt/odoo/addons`, branch main)

A cicdoo monitor does `git fetch` + `git reset --hard origin/main`, then the
restart handler **kills odoo, fetches, resets, restarts with `-u`**. So:
- Deploy = `git push origin main`; the monitor picks it up (~10 min) and restarts.
- **Gotcha:** if that fetch fails, the handler leaves odoo DOWN. Git auth is via a
  credential helper: `git config --global credential.helper store` +
  `/opt/odoo/.git-credentials` = `https://x-access-token:<token>@github.com`
  (HOME=/opt/odoo, user odoo). Keep the token valid; **rotate it periodically**.
- Emergency recovery if the pipeline leaves odoo dead:
  `nohup venv/bin/python3 ce/odoo-bin -c odoo.conf -d <uuid> &`
  (a later clean deploy hands it back to cicdoo).
- `/var/log/odoo/odoo.log` is truncated in place on each deploy — capture what you
  need first. DB name = instance UUID.

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

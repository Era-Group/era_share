# Era WAHA WhatsApp Integration

Brings the **WAHA** (WhatsApp HTTP API, https://waha.devlike.pro) into Odoo's
standard Enterprise **WhatsApp + Discuss** stack as an alternative backend
*provider*, so WAHA conversations behave exactly like standard WhatsApp inside
Discuss.

## What it does
- **Inbound:** each WAHA sender number becomes a `channel_type='whatsapp'` Discuss
  channel linked to the `res.partner` with the matching phone number.
- **Outbound:** replies typed in Discuss are delivered through WAHA (`/api/sendText`,
  `/api/sendImage`, `/api/sendFile`, `/api/sendVoice`).
- **Delivery/read ticks** from WAHA `message.ack`.
- **Session management + QR** on the account form (start/stop/refresh/QR).
- **Smart history backfill:** imports previous conversation when a number that
  messaged before linking is connected, or after a disconnection. It checks the
  last two messages; if they are unknown, it backfills the gap (deduped,
  idempotent).

## Architecture
Reuses `ee/whatsapp` entirely (channel type, composer, read-ticks, partner
matching, channel creation — no new JavaScript). Only the send/receive backend is
swapped to WAHA:
- `whatsapp.account` gains `provider = meta | waha` + WAHA connection fields.
- `whatsapp.message._send_message` routes WAHA messages to WAHA.
- Controller `POST /era/waha/webhook` ingests WAHA events.
- The 24h Meta window is disabled for WAHA channels.

## Setup
1. Install the module (pulls in `whatsapp`).
2. **WhatsApp → Configuration → Accounts →** create an account, set **Provider = WAHA**,
   fill Server URL / API Key / Session, then **Start Session** and **Get QR Code**;
   scan it until the status is `working`.
3. Point the WAHA session's webhook at `<base_url>/era/waha/webhook` (Start Session
   configures this automatically).

## Coexistence with `sadeem_waha_whatsapp`
Uses a distinct route (`/era/waha/webhook`) and its own account/session config.
**Use a dedicated WAHA session name** for this integration — a WAHA session's
webhook list is replaced on update, so two modules must not share one session.

## Known limitations
- Discuss orders messages by id, not date, so a *later* history backfill into an
  active channel appears at the bottom (with a separator note). Auto-import on
  channel creation keeps correct order.
- Historical outbound (sent from the phone) shows on the operator side with the
  operator's name.
- Live `fromMe` messages are not captured via the webhook (to avoid echo); they
  are picked up by history import / reconciliation.

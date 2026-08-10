# Chatter Cc

Adds a **Cc:** line to the backend chatter composer, next to the existing
**To:** line, and delivers the message so that the copied people really are in
copy.

## What it does

* A `Cc:` row appears under the `To:` row when composing a **message** in the
  chatter. It is hidden for internal notes.
* The row reuses core's `RecipientsInput`, so it has the same autocomplete,
  the same "Create …" / "Search More…" options and the same tag chips as the
  `To:` row. A partner already on `To:` cannot be added to `Cc:`.
* Cc recipients are added to `mail.message.partner_ids`, so they are notified
  through Odoo's standard machinery: their own e-mail, in their own language,
  with their own `mail.notification` record and the usual failure/retry
  handling. Which of them were on the Cc line is recorded separately in the new
  `mail.message.partner_cc_ids` field.
* Every notification e-mail generated for the message carries a real, visible
  `Cc:` header, so readers see who was copied and "Reply All" works.
* Cc survives **Open full composer** (`mail.compose.message` gets a `Cc` field)
  and **Schedule message**.
* The Cc list is shown in the message's recipients popover.

## How the `Cc:` header is produced

The notification mails get a private header `X-Msg-Cc-Add`, and the Cc
addresses are removed from core's `X-Msg-To-Add` so nobody appears twice. At
sending time, `ir.mail_server._alter_message__` moves `X-Msg-Cc-Add` into a
real `Cc:` header and deletes the private one.

That hook runs **after** `_prepare_smtp_to_list` has frozen the envelope
recipients, so the header is display-only and delivers no duplicate copy —
this is exactly the pattern Odoo itself uses for `X-Msg-To-Add`. The Cc people
receive the message through their own notification e-mail instead.

## Technical notes

* No core module is modified; everything lives in this module.
* `_get_message_create_valid_field_names` is extended so `partner_cc_ids` may
  be passed to `message_post()`.
* The `/mail/message/post` controller resolves `partner_cc_ids` /
  `partner_cc_emails` from `post_data` using the same access filtering and the
  same `_partner_find_from_emails_single` rules core applies to the To line.
* The Cc header inherits core's `_CUSTOMER_HEADERS_LIMIT_COUNT` (50) anti-leak
  guard: above that many Cc recipients no header is emitted, though everybody
  is still notified.
* Mass-mailing mode deliberately ignores the Cc field.

---

Era Group · <https://era.net.sa> · License: LGPL-3

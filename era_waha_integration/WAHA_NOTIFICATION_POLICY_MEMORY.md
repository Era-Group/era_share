# WAHA Notification Policy Memory

## Goal

Keep WAHA conversations from being missed without notifying every employee for
every inbound/outbound message. WAHA channels are a shared inbox, not disposable
direct-message chats.

## Current Policy

- Scope: Odoo Discuss only. Do not add email or external alerts.
- Applies to individual WAHA chats and allowlisted WAHA groups.
- A live inbound WAHA message not owned by an internal participant notifies the
  account's `Default Users` (`notify_user_ids`).
- A user becomes a participant by sending a WAHA reply, posting a WAHA internal
  note, or being mentioned in a WAHA internal note.
- Once participants exist, later live inbound messages notify participants only.
- Outbound WAHA messages and imported/history messages do not notify anyone.
- WAHA internal notes notify participants other than the note author.
- A live inbound message unresolved for 60 minutes escalates once to Default
  Users during company working hours.
- A WAHA reply or internal note resolves all pending inbound work in that
  conversation.

## Unread Counter Rule

Discuss broadcasts channel messages to all members for synchronization, while
WAHA access membership is intentionally wider than notification ownership.

- The actual notification audience is stored on `mail.message` in
  `waha_notification_partner_ids`.
- The frontend only suppresses a WAHA unread counter when the backend explicitly
  marks `waha_notification_policy_applied`.
- Non-recipient internal members are advanced past the posted message using
  `new_message_separator`, both after new WAHA messages and after a reply/note.
- Their browser receives the refreshed member state through the Discuss bus, so
  the red sidebar badge and the top Discuss counter are removed immediately.
- Do not rely on frontend suppression alone: Odoo recomputes unread counters from
  `new_message_separator` after a reload.

## Conversation Retention

- WAHA channel history is never deleted by this policy.
- WAHA channels are kept pinned for internal members so they remain in the Discuss
  sidebar, including old conversations.
- `_waha_keep_channel_members_pinned()` also broadcasts each retained channel
  header to its internal member. This is necessary because an open browser can
  retain an old unpinned sidebar cache.
- `DiscussChannel.channel_pin()` refuses to unpin WAHA channels; the sidebar is
  treated as an inbox rather than a temporary chat list.

## Historical Backfill

Migration `19.0.1.18.6` records active internal authors of historical WAHA
outbound messages and `mail.mt_note` messages as participants. It does not
create retrospective notifications, inbound escalations, or new messages.

## Key Implementation Files

- `models/discuss_channel.py`
  - participant relation and tracking
  - WAHA-only recipient filtering
  - unread-counter synchronization
  - WAHA pin protection
- `models/mail_message.py`
  - escalation flags and notification-audience serialization
- `models/whatsapp_account.py`
  - live inbound marker, escalation cron, historical participant/pin backfill
- `static/src/core/common/waha_notification_policy_patch.js`
  - avoids client-side unread increments for explicitly non-recipient WAHA users
- `data/ir_cron_data.xml`
  - unanswered inbound escalation cron
- `migrations/19.0.1.18.3/post-migrate.py`
  - pins old WAHA conversations
- `migrations/19.0.1.18.6/post-migrate.py`
  - seeds participants from old internal activity
- `migrations/19.0.1.18.7/post-migrate.py`
  - broadcasts retained channel headers to active clients

## Current Deployment State

- Module version: `19.0.1.18.7`.
- After modifying Python, manifest, migrations, or assets, use:
  `cicdoo restart era_waha_integration`
- Verify the active database version with:
  `SELECT latest_version FROM ir_module_module WHERE name = 'era_waha_integration';`

## Validation

- `python3 -m py_compile era_waha_integration/models/discuss_channel.py era_waha_integration/models/mail_message.py era_waha_integration/models/whatsapp_account.py`
- `node --input-type=module --check < era_waha_integration/static/src/core/common/waha_notification_policy_patch.js`
- `git diff --check`
- Run Odoo module tests with `--test-tags /era_waha_integration`.

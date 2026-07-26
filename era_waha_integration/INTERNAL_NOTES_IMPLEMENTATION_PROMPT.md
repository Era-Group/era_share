# Implementation Prompt: WAHA Internal Notes In Discuss

Implement an **Internal Note** feature for WAHA WhatsApp Discuss channels in `era_waha_integration`.

## Required Behavior

- Add a clear composer action named `Internal Note` for WAHA WhatsApp channels only.
- A selected internal note must be posted inside the Discuss channel for internal Odoo users.
- It must never create a `whatsapp.message` record.
- It must never call WAHA, send text/media, typing presence, reactions, acknowledgements, seen receipts, or any other WhatsApp API operation.
- It must not be visible to WhatsApp customer partners, portal users, public users, or external group participants.
- Existing normal WAHA sends and inbound messages must remain unchanged.
- Must work for both one-to-one WAHA channels and WAHA group channels.

## Current Context

- Main WAHA channel logic: `models/discuss_channel.py`.
- WAHA send is triggered by standard `discuss.channel.message_post()` when `message_type='whatsapp_message'`; normal internal notes should instead post as a normal Odoo comment with `subtype_xmlid='mail.mt_note'`.
- Client-side message post parameters are built in Odoo core at `@mail/core/common/store_service`, `Store.getMessagePostParams()`.
- Composer quick actions use `registerComposerAction()` from `@mail/core/common/composer_actions`.
- Existing client-side WAHA patches are in `static/src/core/common/` and are automatically bundled through the manifest glob.
- `DiscussChannel.is_waha_channel` is already sent to the client store.
- The current module uses Odoo 19.

## Recommended Design

1. Add a composer quick action in a module JavaScript file under `static/src/core/common/`.
   - Show it only when `composer.targetThread?.is_waha_channel` is true.
   - Use a lock/note icon and clear active state.
   - Store the mode on the composer, for example `composer.wahaInternalNote`.
   - The action must be reset when the composer is cleared or after a successful post.

2. Patch `Store.getMessagePostParams()`.
   - When the composer is in internal-note mode, append a dedicated server parameter, for example `waha_internal_note: true`.
   - Set `message_type: 'comment'` and `subtype_xmlid: 'mail.mt_note'`.
   - Do not set `message_type: 'whatsapp_message'`.

3. Add a server-side `mail.message` boolean field such as `waha_internal_note`.
   - Ensure the dedicated message-post parameter is accepted by the target `discuss.channel` model. Add it to `_get_notify_valid_parameters()` if Odoo filtering requires it.
   - In a `DiscussChannel` hook such as `_message_post_after_hook`, mark the created message when this parameter is passed and the channel is WAHA.
   - Do not add a WAHA outbound record for these messages. This follows naturally if the posted message is not `whatsapp_message`; verify it.

4. Enforce visibility server-side, not only through frontend styling.
   - Internal users may read the note.
   - Portal/public/external users must not get it from normal thread fetches, notifications, bus serialization, or direct `mail.message` reads.
   - Prefer Odoo's existing mail-message access/filter hooks and record-rule conventions where applicable. Do not rely only on hiding the note in JavaScript.
   - Carefully verify whether WhatsApp external partners are members on existing 1:1 channels. Group participants are not members by design.

5. Optional display polish:
   - Make the internal note visually distinguishable in Discuss using the standard `mail.mt_note` behavior. Do not make it resemble a WhatsApp-sent message.

## Validation Checklist

- In a WAHA 1:1 channel, toggle Internal Note and post text and attachment.
  - It appears to internal users.
  - No row is created in `whatsapp_message` for it.
  - No WAHA HTTP request is emitted.
  - Customer/portal account cannot read it.
- In a WAHA group channel, the same behavior holds.
- Toggle back to normal send and verify text/media still reach WAHA.
- Verify internal notes are not included in WhatsApp history import/reconciliation or sent as outbound messages.
- Run Python compile checks and targeted Odoo tests if available.
- Do not push unless explicitly requested.

# ERA WhatsApp Floating Discuss

A floating, draggable and minimizable window that embeds the **standard Odoo
Discuss** experience, scoped to **WhatsApp** conversations (the official
`whatsapp` Enterprise module — `discuss.channel` records with
`channel_type = 'whatsapp'`).

## What it does

* A persistent **WhatsApp bubble** sits at the bottom-right of every backend
  page. Click it to open the floating window. A WhatsApp icon is also added to
  the top systray.
* The window shows a **WhatsApp-only conversation list** on the side and the
  **real Discuss thread + composer** (message history, attachments, delivery
  ticks, reactions, …) in the main pane — it reuses the genuine Discuss OWL
  components, so it inherits every Discuss feature automatically.
* **Minimize** (chevron-down) or **close** collapses the window back to the
  bubble, so it can be reopened with a single click.
* The header is a **drag handle**; the bottom-end corner is a **resize grip**.

## How it works (technical)

* The window is registered once in the `main_components` registry through a
  service `start()` — the same mechanism the Discuss *ChatHub* uses — so it
  lives on top of the whole web client and survives navigation.
* The main pane renders `<Discuss hasSidebar="false"/>`
  (`@mail/core/public_web/discuss`). The side list reuses
  `DiscussSidebarChannel`
  (`@mail/discuss/core/public_web/discuss_sidebar_categories`) over
  `store.discuss.whatsapp.threads`.
* While the window is open it sets `store.discuss.isActive = true`, which makes
  clicking a conversation **select it inside the embedded pane** (instead of
  spawning a separate chat window) and restores the previous value on close.

## Visibility

The bubble, window and systray icon render **only for users in the WhatsApp
access group** (`whatsapp.group_whatsapp_admin`). Everyone else sees nothing.
The check uses `user.hasGroup(...)` at boot. To target a different group, change
`WHATSAPP_GROUP` in `whatsapp_float.js`.

## Data source

This module shows the conversations from the **official Odoo WhatsApp**
integration. It does **not** read the third-party WAHA module — WAHA stores its
messages in its own models and never creates `discuss.channel` records, so its
conversations do not appear in Discuss.

## Dependencies

`mail`, `web`, `whatsapp` (Enterprise). Depending on `whatsapp` guarantees the
WhatsApp Discuss category (`store.discuss.whatsapp`) exists and that this module
loads after it.

## Install / update

This is a frontend-asset module, so its assets are only served after an Odoo
**upgrade** that rebuilds the `web.assets_backend` bundle:

```bash
# pick up the new module, then install it
odoo -c /opt/odoo/odoo.conf -d <db> -i era_whatsapp_float --stop-after-init
# subsequent JS/XML/SCSS changes:
odoo -c /opt/odoo/odoo.conf -d <db> -u era_whatsapp_float --stop-after-init
```

Then hard-refresh the browser (assets are cached). On a running instance you
can instead use **Apps → Update Apps List → install “ERA WhatsApp Floating
Discuss”**.

# Part of the ERA Group custom addon layer. License: LGPL-3.
{
    "name": "ERA AI Accounts",
    "summary": "Link AI provider accounts (Claude via local CLI proxy, OpenAI/Gemini/Cloudflare/custom via key), "
               "share them with users, generate text or images, and pick models dynamically per account.",
    "description": """
ERA AI Accounts
===============

Supersedes ``era_odoo_ai_ext`` and extends Odoo 19's standard ``ai`` / ``ai_app``
stack with first-class **AI accounts**:

* **Local CLI proxy** transport — route Claude through the Claude Code CLI that is
  already authenticated on this server (the "connected account"), so AI works
  without per-token API billing. The first-party ``claude`` binary makes the call
  itself under its own auth; we never replay its OAuth token to the raw API.
* **API-key transport** — OpenAI, Google Gemini, Anthropic (Messages API) and any
  OpenAI-compatible custom provider, with credentials stored per account
  (encrypted at rest, restricted to AI Account Managers).
* **Shared vs personal accounts** — one account for everyone or per-user accounts,
  with admin-controlled sharing (``owner`` + ``allowed users`` + record rules).
* **Dynamic model catalog** — models are synced per account (curated set for the
  CLI proxy; live ``/models`` for key accounts) instead of a hard-coded list.

See the module README for configuration and the compliance note about using a
subscription-backed CLI to serve multiple users.
""",
    "version": "19.0.1.6.1",
    "category": "Productivity/AI",
    "author": "Era Group",
    "license": "LGPL-3",
    "images": ["static/description/icon.png"],
    "depends": ["ai", "ai_app"],
    "data": [
        "security/era_ai_accounts_groups.xml",
        "security/era_ai_accounts_security.xml",
        "security/ir.model.access.csv",
        "views/era_ai_account_views.xml",
        "views/ai_agent_views.xml",
        "views/res_config_settings_views.xml",
        "views/era_ai_accounts_menus.xml",
    ],
    "installable": True,
    "application": False,
    # The custom_llm OpenAI-compatible provider that era_odoo_ai_ext used to add is
    # absorbed here; the old module is reduced to a dependency shim on upgrade.
}

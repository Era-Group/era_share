# Part of the ERA Group custom addon layer. License: LGPL-3.
{
    "name": "ERA AI Accounts",
    "summary": "Link AI provider accounts (Claude/ChatGPT/GLM/Kimi via local CLI proxy — no API key — "
               "or OpenAI/Gemini/Cloudflare/Z.AI-GLM/Kimi/AssemblyAI/custom via key), share them with users, "
               "generate text or images, and pick models dynamically per account.",
    "description": """
ERA AI Accounts
===============

Supersedes ``era_odoo_ai_ext`` and extends Odoo 19's standard ``ai`` / ``ai_app``
stack with first-class **AI accounts**:

* **Local CLI proxy** transport — route Claude through the Claude Code CLI,
  OpenAI through the Codex CLI, or Kimi through Moonshot's Kimi Code CLI,
  already authenticated on this server (the "connected account"), so AI works
  without per-token API billing. The first-party ``claude`` / ``codex`` /
  ``kimi`` binary makes the call itself under its own auth; we never replay its
  OAuth tokens to the raw API.
* **Login with Claude / Connect ChatGPT / Connect Kimi** — link a Claude
  subscription in-app (OAuth, no API key), a ChatGPT subscription (device code or
  the Codex CLI's ``auth.json``), or a Kimi Code subscription (device code),
  stored once on the server and used by every user in the system.
* **API-key transport** — OpenAI, Google Gemini, Anthropic (Messages API),
  Cloudflare Workers AI, **Z.AI (GLM / Zhipu)**, **Kimi (Moonshot AI)**,
  **AssemblyAI** and any
  OpenAI-compatible custom provider, with credentials stored per account
  (encrypted at rest, restricted to AI Account Managers).
* **Z.AI (GLM) two ways** — either the OpenAI-compatible **API key** route, or
  the **CLI proxy** (the Claude binary pointed at Z.AI's Anthropic-compatible
  endpoint with a GLM Coding Plan key — flat-rate, no per-token billing).
* **Kimi (Moonshot AI) three ways** — the **Kimi Code plan** (flat monthly
  subscription at ``api.kimi.com/coding``, Anthropic protocol) through the CLI
  proxy, the pay-per-token **Open Platform** (``api.moonshot.ai/v1``) through
  either the CLI proxy or a direct API key, all running the first-party ``kimi``
  binary fenced down to pure text generation (no tools, one step, empty working
  directory, built-in agent persona replaced).
* **Speech-to-text** — transcribe audio through an OpenAI API-key account
  (Whisper / ``gpt-4o-transcribe``) or AssemblyAI (``universal-2``) via
  ``account.transcribe()``; the CLI proxies are text-only.
* **Shared vs personal accounts** — one account for everyone or per-user accounts,
  with admin-controlled sharing (``owner`` + ``allowed users`` + record rules).
* **Dynamic model catalog** — models are synced per account (curated set for the
  CLI proxy; live ``/models`` for key accounts) instead of a hard-coded list.

See the module README for configuration and the compliance note about using a
subscription-backed CLI to serve multiple users.
""",
    "version": "19.0.1.18.0",
    "category": "Productivity/AI",
    "author": "Era Group",
    "license": "LGPL-3",
    "images": ["static/description/icon.png"],
    "depends": ["ai", "ai_app"],
    "data": [
        "security/era_ai_accounts_groups.xml",
        "security/era_ai_accounts_security.xml",
        "security/ir.model.access.csv",
        "wizards/era_ai_account_login_views.xml",
        "views/era_ai_account_views.xml",
        "views/ai_agent_views.xml",
        "views/res_config_settings_views.xml",
        "views/era_ai_accounts_menus.xml",
    ],
    "assets": {
        # Mirror the bundles where core ``ai`` loads its discuss "post" patch
        # (the one that calls /ai/generate_response), so the refetch fallback is
        # present wherever the Ask AI chat runs: backend, public, portal, livechat.
        "web.assets_backend": [
            "era_ai_accounts/static/src/discuss/core/common/ai_chat_refetch_patch.js",
        ],
        "mail.assets_public": [
            "era_ai_accounts/static/src/discuss/core/common/ai_chat_refetch_patch.js",
        ],
        "portal.assets_chatter_helpers": [
            "era_ai_accounts/static/src/discuss/core/common/ai_chat_refetch_patch.js",
        ],
        "im_livechat.assets_embed_core": [
            "era_ai_accounts/static/src/discuss/core/common/ai_chat_refetch_patch.js",
        ],
    },
    "installable": True,
    "application": False,
    # The custom_llm OpenAI-compatible provider that era_odoo_ai_ext used to add is
    # absorbed here; the old module is reduced to a dependency shim on upgrade.
}

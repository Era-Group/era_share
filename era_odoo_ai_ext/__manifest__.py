{
    "name": "ERA Odoo AI Extension",
    "summary": "Extend Odoo AI with a fully configurable custom LLM provider",
    "description": """
ERA Odoo AI Extension
=====================

This module extends Odoo 19 standard `ai` module and adds support for a fully
custom LLM provider (OpenAI-compatible API), without changing Odoo core files.

What this module adds
---------------------
- New provider option in Odoo AI: `custom_llm`.
- Extra configuration fields in the AI settings tab (`settings#ai_app` > Providers).
- Runtime patch for Odoo AI API service to use your configured provider for:
  - chat / agent responses
  - tool-calling flow
  - embeddings (RAG)

Configuration fields
--------------------
- Provider Name
- Provider API Key
- Provider Base URL
- Authorization Header
- Authorization Prefix
- Chat Model
- Embedding Model
- Referer (optional)
- App Name (optional)

Usage steps
-----------
1. Install the module `era_odoo_ai_ext`.
2. Go to Settings > AI tab (`settings#ai_app`) > Providers.
3. Enable "Use your own custom LLM provider".
4. Fill provider settings:
   - API key
   - Base URL
   - Auth header/prefix
   - Chat model
   - Embedding model
5. Save settings.
6. Open AI Agents and set model to `Custom LLM (Configured Model)`.
7. Test AI chat / Ask AI / RAG scenarios.

Notes
-----
- Best compatibility is with providers exposing OpenAI-style endpoints
  (for responses and embeddings).
- Defaults are OpenRouter-friendly, but any compatible provider can be used.
""",
    "version": "19.0.1.0.1",
    "category": "Tools",
    "author": "Era Group",
    "license": "LGPL-3",
    "depends": ["ai", "ai_app"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "application": False,
}

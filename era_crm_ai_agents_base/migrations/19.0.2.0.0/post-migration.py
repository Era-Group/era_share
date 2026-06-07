# -*- coding: utf-8 -*-
"""Architecture pivot cleanup: native AI (OpenAI/Google) only.

The previous design called providers directly via a multi-provider LLMRouter and
seeded Anthropic / ALLaM / local catalog models. The final design routes every
call through Odoo native AI (OpenAI + Google only) with the LLMRouter repurposed
as the AI Compliance Guard. Drop the now-invalid catalog rows so the catalog
matches what native AI can actually call.

These rows were seeded noupdate=1, so they are not removed automatically — this
script removes them (and their ir.model.data) explicitly.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Model = env["crm.ai.model"]
    stale = Model.with_context(active_test=False).search([
        "|",
        ("provider", "not in", ("openai", "google")),  # anthropic/allam/local
        ("code", "=", "gpt-4o-mini"),                    # old seed, not in native list
    ])
    if stale:
        _logger.info(
            "era_crm_ai_agents_base: removing %d stale catalog model(s) after "
            "the native-AI pivot: %s",
            len(stale), stale.mapped("code"),
        )
        stale.unlink()

# -*- coding: utf-8 -*-
"""19.0.5.0.0 — crm.ai.usage gains usage_type (+ record_ref).

usage_type splits the cost stream into 'llm' vs 'source_api' (the Lead-Gen
source-API cost half). It is REQUIRED with default 'llm'. Odoo's _init_column
would already backfill existing rows to the default when it creates the column,
but we pre-create + backfill here explicitly (belt-and-braces) so the column is
guaranteed non-NULL before any view/constraint sees it — and, critically, so
EVERY pre-existing usage row (Compliance, native Ask-AI, every agent) is
unambiguously stamped 'llm' and nothing is misread as a source-API row.

record_ref (a Reference, nullable) needs no backfill — empty means "unattributed
/ batch usage", which is exactly right for every historical row.
"""


def migrate(cr, version):
    # Create the column if the ORM hasn't yet, then stamp all existing rows 'llm'.
    cr.execute(
        "ALTER TABLE crm_ai_usage ADD COLUMN IF NOT EXISTS usage_type varchar")
    cr.execute(
        "UPDATE crm_ai_usage SET usage_type = 'llm' WHERE usage_type IS NULL")

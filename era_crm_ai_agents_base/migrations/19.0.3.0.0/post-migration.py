# -*- coding: utf-8 -*-
"""Tidy up after the configurable-layers + slim-rate-card changes.

- crm.ai.model lost tier / max_context / env_key_param (it is now a pricing rate
  card, not a catalog).
- crm.ai.agent lost default_model_id (selection moved to model_code /
  model_code_advanced).

Removing a field leaves an orphan DB column behind; drop them so the schema
matches the models. Idempotent and safe (IF EXISTS).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE crm_ai_model
            DROP COLUMN IF EXISTS tier,
            DROP COLUMN IF EXISTS max_context,
            DROP COLUMN IF EXISTS env_key_param
    """)
    cr.execute("ALTER TABLE crm_ai_agent DROP COLUMN IF EXISTS default_model_id")
    _logger.info("era_crm_ai_agents_base: dropped orphan columns after the slim "
                 "rate-card / selection-on-agent change.")

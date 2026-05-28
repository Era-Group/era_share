"""ERA GEO — AI bridge extensions.

Extends ``era_seo_ai``'s registries at addon load so the GEO finding
``geo_no_answer_summary`` flows through the existing field-fix workflow:

  - ``AI_FIXABLE_CODES``  : adds the GEO code, so ``ai_supported`` becomes
    True on those findings (the field is computed, non-stored — picks up
    the extended set on the next read, no migration needed).
  - ``_FIELD_MAP``        : maps the code to ``geo_answer_summary``
    (``needs_ai=True``). The existing per-language text-field fix handles
    the rest — prompt building, JSON parsing, translation write.

This file imports the target modules and mutates their module-level
registries. Idempotent: re-runs (e.g. registry reload) are a no-op because
sets and dict[key]=value are commutative.
"""
import logging

from odoo.addons.era_seo_ai.models import ai_client, seo_audit_finding

_logger = logging.getLogger(__name__)

_NEW_CODE = 'geo_no_answer_summary'
_TARGET_FIELD = 'geo_answer_summary'

# 1. Mark the GEO finding as AI-fixable.
seo_audit_finding.AI_FIXABLE_CODES.add(_NEW_CODE)

# 2. Tell the AI client which field to write.
ai_client._FIELD_MAP[_NEW_CODE] = (_TARGET_FIELD, True)

_logger.info(
    'era_geo_ai: registered AI fix for %s -> %s', _NEW_CODE, _TARGET_FIELD)

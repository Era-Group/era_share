"""Neutralize any pending AI findings that would rewrite a public URL.

From 19.0.3.3.5 the suite never changes a page/blog public URL (URL-PROTECTION
POLICY): the slug checks are out of _FIELD_MAP / AI_FIXABLE_CODES and the apply
chokepoint refuses url/name/slug writes.

On an already-deployed install some slug findings may still be sitting in
ai_status='suggested' with ai_proposed_field='url' (or 'name'/'slug'). The
finding form gates its "Apply" button on ai_status alone, so those legacy
proposals would still offer an Apply that now (correctly) errors. Reset them
once to informational-only so the stale Apply button disappears; the apply-time
guard remains the backstop.

Already-applied findings (ai_status='applied') are left untouched — we do not
revert URLs that were changed by a previous version. Idempotent.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

# Historical slug check codes that used to map to the 'url' field, plus the
# set of fields whose value forms (or derives) a record's public URL. Defined
# locally so this migration is a self-contained point-in-time snapshot.
_SLUG_CODES = ('slug_contains_uppercase', 'slug_contains_stopwords', 'slug_too_long')
_URL_PROTECTED_FIELDS = ('url', 'name', 'website_url', 'seo_name', 'slug')


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'era.seo.audit.finding' not in env:
        return
    Finding = env['era.seo.audit.finding'].sudo()
    pending = Finding.search([
        ('ai_status', 'in', ['suggested', 'none', 'failed', 'manual_review']),
        '|',
        ('check_code', 'in', list(_SLUG_CODES)),
        ('ai_proposed_field', 'in', list(_URL_PROTECTED_FIELDS)),
    ])
    if not pending:
        _logger.info('disable-url-fixes: no pending URL-rewriting findings to reset')
        return
    pending.write({
        'ai_status': 'not_supported',
        'ai_proposed_value': False,
        'ai_proposed_translations': False,
        'ai_proposed_field': False,
        'ai_fix_payload': False,
        'ai_needs_manual_review': False,
        'ai_review_reason': False,
    })
    _logger.info(
        'disable-url-fixes: reset %d pending finding(s) that would have '
        'rewritten a public URL to informational-only', len(pending))

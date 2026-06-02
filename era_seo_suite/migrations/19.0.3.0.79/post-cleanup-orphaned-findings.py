"""Delete audit findings whose target website.page no longer exists.

Pages deleted before 19.0.3.0.79 left orphaned findings behind (website.page
unlink didn't clean findings until this version). An orphaned finding makes the
AI 'Suggest Fix' fail with 'target record vanished'. Remove them once; the
website.page.unlink override prevents new ones.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'era.seo.audit.finding' not in env:
        return
    Finding = env['era.seo.audit.finding'].sudo()
    existing = set(env['website.page'].sudo().search([]).ids)
    orphans = Finding.search([('res_model', '=', 'website.page')]).filtered(
        lambda f: f.res_id not in existing)
    n = len(orphans)
    if orphans:
        orphans.unlink()
    _logger.info('cleanup-orphaned-findings: removed %d finding(s) whose '
                 'target page no longer exists', n)

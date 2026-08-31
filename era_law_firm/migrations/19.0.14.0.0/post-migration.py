"""Re-run conflict checks that were cleared by the old exact-match rule.

Matching on partner_id alone meant a client entered twice passed the check.
Every check already marked clear was decided under that rule, so each one is
a conclusion that may not hold — and a stale "no conflict" is exactly the
record a firm would rely on.

Only checks in the 'clear' state are re-run: 'blocked' and 'overridden' were
decisions someone made, and a migration has no business reopening those.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    checks = env['legal.conflict.check'].search([('state', '=', 'clear')])
    if not checks:
        return
    checks.action_run_check()
    now_blocked = checks.filtered(lambda c: c.state == 'blocked')
    _logger.info(
        'Re-ran %s conflict check(s) under the wider match; %s are now blocked',
        len(checks), len(now_blocked))
    for check in now_blocked:
        bases = ', '.join(sorted(set(check.line_ids.mapped('match_basis'))))
        _logger.warning('Case %s now shows a conflict (%s) — review before proceeding',
                        check.case_id.display_name, bases)

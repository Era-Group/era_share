"""Recompute every stored party signature under the hashed format.

Two reasons, one pass. The old signatures embedded raw identity numbers in a
field lawyers can read, so they have to be scrubbed from the database, not
merely stopped being written. And the format changed to a hash, so every
stored value would mismatch the new computation and demand a needless re-run
on any draft case with a clear check.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    checks = env['legal.conflict.check'].search([('party_signature', '!=', False)])
    for check in checks:
        check.party_signature = check.case_id._party_signature()
    _logger.info('Re-signed %s conflict check(s); raw identities scrubbed', len(checks))

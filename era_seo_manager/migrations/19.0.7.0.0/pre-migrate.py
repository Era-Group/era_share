"""Pre-migration for 19.0.7.0.0 — make the finding key language-aware.

Findings gain a ``lang_id`` and the unique key becomes
(check_code, res_model, res_id, COALESCE(lang_id, 0)). The old
(check_code, res_model, res_id) unique index would now wrongly block two
findings that differ only by language, so drop it here. The new index is
created by the model's init() during this same upgrade.

Existing rows have lang_id = NULL (the column is added by the ORM right
after this script), so they remain unique under the new COALESCE key — no
data de-dup is required beyond what 19.0.6.1.0 already did.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("DROP INDEX IF EXISTS era_seo_audit_finding_key_uidx")
    _logger.info(
        'era_seo_manager 19.0.7.0.0: dropped legacy finding unique index; '
        'language-aware index will be recreated by init().'
    )

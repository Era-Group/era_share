"""Pre-migration for 19.0.6.1.0 — de-duplicate audit findings.

Until now every audit run created a fresh finding row, so the same defect
on the same page accumulated one row per run. 19.0.6.1.0 makes findings
upsert by (check_code, res_model, res_id) and adds a UNIQUE index — which
would fail on the existing duplicate rows. This pre-migration collapses
each duplicate group to a single row (keeping the most recent, and
preserving "resolved" if ANY row in the group was resolved) before the
model's init() creates the index.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Table may not exist yet on a fresh install path; guard.
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'era_seo_audit_finding'
    """)
    if not cr.fetchone():
        return

    # Carry "resolved" forward: if any duplicate in a group was resolved,
    # keep the survivor resolved so we don't re-surface a fixed issue.
    cr.execute("""
        UPDATE era_seo_audit_finding f
        SET is_resolved = TRUE
        FROM (
            SELECT check_code, res_model, res_id
            FROM era_seo_audit_finding
            WHERE is_resolved = TRUE
            GROUP BY check_code, res_model, res_id
        ) r
        WHERE f.check_code = r.check_code
          AND f.res_model IS NOT DISTINCT FROM r.res_model
          AND f.res_id IS NOT DISTINCT FROM r.res_id
    """)

    # Delete all but the newest row (max id) per (check_code, res_model, res_id).
    cr.execute("""
        DELETE FROM era_seo_audit_finding
        WHERE id NOT IN (
            SELECT MAX(id)
            FROM era_seo_audit_finding
            GROUP BY check_code, res_model, res_id
        )
    """)
    removed = cr.rowcount
    _logger.info(
        'era_seo_manager 19.0.6.1.0: removed %d duplicate audit finding(s).',
        removed,
    )

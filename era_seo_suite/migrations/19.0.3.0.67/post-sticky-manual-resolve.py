"""Protect existing resolved findings from being reopened by a later audit.

A new `resolved_manually` flag makes manual 'Mark Resolved' sticky (the audit
no longer reopens what a user deliberately dismissed). The flag defaults to
False, so without this backfill every already-resolved finding would reopen on
the next re-detection. We can't perfectly tell apart historically manual vs
auto resolutions (both stamped resolved_user_id), so we treat ALL currently
resolved findings as dismissed — the safe, trust-preserving choice: nothing a
user previously resolved gets undone. Going forward _auto_resolve_fixed stamps
resolved_manually=False, so genuinely auto-resolved findings reopen on regress.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE era_seo_audit_finding
        SET resolved_manually = TRUE
        WHERE is_resolved = TRUE
          AND resolved_manually IS NOT TRUE
    """)

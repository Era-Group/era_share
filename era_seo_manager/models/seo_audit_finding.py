"""ERA SEO — single audit finding.

One row per (run, check, target) triple. Created by the runner during a
``era.seo.audit.run`` execution.

Per SPEC §13.1.2.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


SEVERITY_SELECTION = [
    ('critical', 'Critical'),
    ('warning', 'Warning'),
    ('info', 'Info'),
]


class EraSeoAuditFinding(models.Model):
    _name = 'era.seo.audit.finding'
    _description = 'SEO Audit Finding'
    _order = 'severity, check_code, id'

    name = fields.Char(string='Name', compute='_compute_name', store=True)

    run_id = fields.Many2one(
        'era.seo.audit.run',
        string='Last Detected In',
        ondelete='set null',
        index=True,
        help='The most recent audit run that detected this finding. Findings '
             'persist across runs (upserted by check + target), so deleting '
             'an old run no longer deletes its findings.',
    )
    severity = fields.Selection(
        SEVERITY_SELECTION,
        required=True,
        index=True,
    )
    check_code = fields.Char(string='Check Code', required=True, index=True)
    check_name = fields.Char(string='Check Name', required=True)

    res_model = fields.Char(string='Target Model', index=True)
    res_id = fields.Integer(string='Target ID', index=True)
    url = fields.Char(string='URL')

    details = fields.Text(string='Details')
    suggested_fix = fields.Text(string='Suggested Fix')

    is_resolved = fields.Boolean(
        string='Resolved',
        default=False,
        help='Tick when this finding has been addressed. Resolved findings '
             'are hidden from the default dashboard view.',
    )
    resolved_date = fields.Datetime(string='Resolved Date', readonly=True)
    resolved_user_id = fields.Many2one(
        'res.users', string='Resolved By', readonly=True,
    )

    def init(self):
        """One open finding per (check_code, res_model, res_id).

        Enforces the upsert key at the DB level so concurrent runs can't
        race a duplicate in. The 19.0.6.1.0 migration de-dups existing rows
        before this index is created.
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS era_seo_audit_finding_key_uidx
            ON era_seo_audit_finding (check_code, res_model, res_id)
        """)

    @api.depends('check_name', 'url', 'res_model', 'res_id')
    def _compute_name(self):
        for rec in self:
            target = rec.url or '{}#{}'.format(rec.res_model or '?', rec.res_id or 0)
            rec.name = '{} — {}'.format(rec.check_name or rec.check_code or '?', target)

    def action_mark_resolved(self):
        self.write({
            'is_resolved': True,
            'resolved_date': fields.Datetime.now(),
            'resolved_user_id': self.env.user.id,
        })

    def action_mark_unresolved(self):
        self.write({
            'is_resolved': False,
            'resolved_date': False,
            'resolved_user_id': False,
        })

    def action_open_target(self):
        """Open the offending record in its native form view."""
        self.ensure_one()
        if not (self.res_model and self.res_id):
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'target': 'current',
        }

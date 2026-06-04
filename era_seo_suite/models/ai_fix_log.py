"""ERA SEO AI — audit log of AI fix calls.

One row per ``ai.agent`` call. Captures the request (defect, target), the
response (proposed value, explanation, confidence, which agent/model
answered), whether it was applied, and any error.

Token usage is intentionally not tracked: Odoo's
``ai.agent.get_direct_response`` does not surface per-call usage to the
caller. Cost reporting, if needed, lives in the AI app.
"""
from odoo import api, fields, models


class EraSeoAiFixLog(models.Model):
    _name = 'era.seo.ai.fix.log'
    _description = 'SEO AI Auto-Fix Log'
    _order = 'create_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)

    kind = fields.Selection(
        [('fix', 'Finding Fix'), ('fill', 'Full SEO Fill')],
        default='fix',
        index=True,
        help='"Finding Fix" patches one audit finding; "Full SEO Fill" '
             'generates all recommended meta fields on a record at once.',
    )

    finding_id = fields.Many2one(
        'era.seo.audit.finding',
        string='Audit Finding',
        ondelete='set null',
        index=True,
    )
    check_code = fields.Char(string='Check Code', index=True)
    target_model = fields.Char(string='Target Model')
    target_id = fields.Integer(string='Target ID')
    target_url = fields.Char(string='Target URL')

    model = fields.Char(string='AI Model / Agent')
    field_written = fields.Char(string='Field')

    proposed_value = fields.Text(string='Proposed Value')
    explanation = fields.Text()
    confidence = fields.Float(string='Confidence', digits=(3, 2))

    autopilot_decision = fields.Selection(
        [
            ('suggested', 'Suggested'),
            ('auto_applied', 'Auto-Applied'),
            ('review', 'Sent to Review'),
            ('blocked', 'Stopped'),
            ('failed', 'Failed'),
        ],
        string='Autopilot Decision',
        readonly=True,
        index=True,
        help='How the unattended workflow handled this proposal. Empty means '
             'a manual user action created the log.',
    )
    autopilot_reason = fields.Text(
        string='Autopilot Reason',
        readonly=True,
        help='Why the unattended workflow applied, queued, stopped, or failed '
             'this proposal.',
    )

    applied = fields.Boolean(string='Applied', default=False)
    applied_date = fields.Datetime(readonly=True)
    applied_user_id = fields.Many2one('res.users', readonly=True)

    error_message = fields.Text(readonly=True)

    triggered_user_id = fields.Many2one(
        'res.users',
        string='Triggered By',
        default=lambda self: self.env.user.id,
        readonly=True,
    )

    @api.depends('check_code', 'target_url')
    def _compute_name(self):
        for rec in self:
            rec.name = 'AI Fix · {} · {}'.format(
                rec.check_code or '?', rec.target_url or '?',
            )

"""ERA SEO AI — audit log of API calls.

One row per Claude call. Captures the request (defect, prompt, target), the
response (proposed value, explanation, confidence), token usage (so admins
can see the cache-hit rate over time), and the user who triggered the call.

Token usage in particular is the only way to confirm prompt caching is
actually working — ``cache_read_input_tokens`` should be > 0 on every call
after the first one in any 5-minute window.
"""
from odoo import api, fields, models


class EraSeoAiFixLog(models.Model):
    _name = 'era.seo.ai.fix.log'
    _description = 'SEO AI Auto-Fix Log'
    _order = 'create_date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)

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

    model = fields.Char(string='Claude Model')
    field_written = fields.Char(string='Field')

    proposed_value = fields.Text(string='Proposed Value')
    explanation = fields.Text()
    confidence = fields.Float(string='Confidence', digits=(3, 2))

    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    cache_read_input_tokens = fields.Integer(readonly=True)
    cache_creation_input_tokens = fields.Integer(readonly=True)
    cache_hit = fields.Boolean(
        string='Cache Hit',
        compute='_compute_cache_hit',
        store=True,
        help='True when the request was served at least partly from the prompt cache.',
    )

    applied = fields.Boolean(
        string='Applied',
        default=False,
        help='True when the proposed value was written back to the target record.',
    )
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

    @api.depends('cache_read_input_tokens')
    def _compute_cache_hit(self):
        for rec in self:
            rec.cache_hit = bool(rec.cache_read_input_tokens)

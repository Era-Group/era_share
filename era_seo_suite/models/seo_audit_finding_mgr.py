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
    lang_id = fields.Many2one(
        'res.lang',
        string='Language',
        ondelete='cascade',
        index=True,
        help='For per-language content checks (title / description), the '
             'language this finding is about. Empty for language-agnostic '
             'checks (slug, H1, schema, redirects).',
    )
    url = fields.Char(string='URL')

    details = fields.Text(string='Details')
    suggested_fix = fields.Text(string='Suggested Fix')

    is_resolved = fields.Boolean(
        string='Resolved',
        default=False,
        index=True,
        help='Tick when this finding has been addressed. Resolved findings '
             'are hidden from the default dashboard view.',
    )
    resolved_date = fields.Datetime(string='Resolved Date', readonly=True)
    resolved_user_id = fields.Many2one(
        'res.users', string='Resolved By', readonly=True,
    )
    resolved_manually = fields.Boolean(
        string='Dismissed by user', readonly=True,
        help='True when a person clicked Mark Resolved (a deliberate '
             'dismissal). Such findings are NOT reopened by a later audit even '
             'if the defect is still detectable — only auto-resolved findings '
             'reopen on re-detection.',
    )

    def init(self):
        """One open finding per (check_code, res_model, res_id, lang).

        Language is part of the key so a defect can be reported independently
        per installed website language (e.g. the English description is too
        short while the Arabic one is fine). lang_id is NULL for
        language-agnostic checks; COALESCE keeps those unique too. The
        19.0.6.1.0 / 19.0.7.0.0 migrations de-dup existing rows before this
        index is created.
        """
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS era_seo_audit_finding_key_lang_uidx
            ON era_seo_audit_finding
            (check_code, res_model, res_id, COALESCE(lang_id, 0))
        """)

    @api.depends('check_name', 'url', 'res_model', 'res_id')
    def _compute_name(self):
        for rec in self:
            target = rec.url or '{}#{}'.format(rec.res_model or '?', rec.res_id or 0)
            rec.name = '{} — {}'.format(rec.check_name or rec.check_code or '?', target)

    def action_mark_resolved(self):
        # Manual dismissal — stays resolved across future audits (sticky).
        self.write({
            'is_resolved': True,
            'resolved_date': fields.Datetime.now(),
            'resolved_user_id': self.env.user.id,
            'resolved_manually': True,
        })

    def action_mark_unresolved(self):
        self.write({
            'is_resolved': False,
            'resolved_date': False,
            'resolved_user_id': False,
            'resolved_manually': False,
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

    def action_open_public_url(self):
        """Open the public-facing page for this finding in a new tab.

        Resolves the absolute URL by prefixing the finding's stored path
        with ``web.base.url`` (Odoo's canonical site URL). The finding's
        URL field stores the path the audit walked (e.g. ``/blog/foo``,
        ``/contactus``); we don't have a per-record website-aware URL
        builder here, so the base URL fallback works for single-website
        installs and is the right approximation for multi-website ones
        too — admins can re-pick the website later if needed.

        Returns an ``ir.actions.act_url`` with ``target='new'`` so the
        backend stays open in the original tab.
        """
        self.ensure_one()
        path = (self.url or '').strip()
        if not path:
            return False
        if path.startswith(('http://', 'https://')):
            full_url = path
        else:
            base = (self.env['ir.config_parameter'].sudo()
                    .get_param('web.base.url', '') or '').rstrip('/')
            if not path.startswith('/'):
                path = '/' + path
            full_url = base + path if base else path
        return {
            'type': 'ir.actions.act_url',
            'url': full_url,
            'target': 'new',
        }

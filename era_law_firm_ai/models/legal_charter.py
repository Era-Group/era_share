"""The office's standing legal instructions, shared by every agent.

One editable charter is prepended to every request through Odoo's
`extra_system_context`, so the office's position on how legal work is done lives
in one record instead of being copied into four system prompts and drifting apart.

The closing disclaimer is appended to the answer in code rather than left to the
model. A notice that only appears when the model remembers to write it is not a
notice, and this one carries the office's liability position.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class LegalAICharter(models.Model):
    _name = 'legal.ai.charter'
    _description = 'Legal AI Charter'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', index=True, default=lambda self: self.env.company,
        help="Leave empty to apply to every company.")
    body = fields.Text(
        string='Standing Instructions', required=True, translate=True,
        help="Prepended to every request, whichever agent handles it. This is where the office "
             "states how legal work is to be done -- the framework, the sources of truth, the "
             "drafting conventions. Each agent's own prompt then covers its particular task.")
    disclaimer = fields.Text(
        string='Mandatory Notice', required=True, translate=True,
        help="Appended to every answer by the system, not by the model. A notice that appears only "
             "when the model remembers to write it is not a notice.")
    reference_portal = fields.Char(
        string='Sole Legislative Authority', required=True,
        default='https://laws.moj.gov.sa/',
        help="The one portal this office treats as authoritative for Saudi legislation. It is "
             "named in the standing instructions so an answer is anchored to it, and it is where "
             "a lawyer verifies a citation. An agent cannot open it -- only text attached as a "
             "source is text the agent has actually read.")
    legislation_ids = fields.Many2many(
        'legal.legislation', string='Legislation Relied On',
        help="The statutes this office works from. Listing one here does not put its text in front "
             "of the agent -- for that the text must be attached to the agent as a source.")

    _one_active_per_company = models.Constraint(
        'UNIQUE(company_id, active)',
        'Only one charter can be active per company.')

    @api.model
    def _reference_portal(self, company=None):
        charter = self._for_company(company or self.env.company)
        return charter.reference_portal or 'https://laws.moj.gov.sa/'

    @api.model
    def _for_company(self, company):
        """The charter that governs this company: its own if it has one, else the shared one.

        Ordering by a many2one delegates to the comodel's own order through a LEFT
        JOIN, so the shared charter's NULL company sorted first under `desc` and won
        every time. Two explicit searches say what is meant and cannot be undone by
        a change to res.company._order.
        """
        own = self.sudo().search([('company_id', '=', company.id)], order='sequence', limit=1)
        return own or self.sudo().search([('company_id', '=', False)], order='sequence', limit=1)

    @api.constrains('body', 'disclaimer', 'reference_portal')
    def _check_not_empty(self):
        for charter in self:
            if not (charter.body or '').strip() or not (charter.disclaimer or '').strip():
                raise ValidationError(_('A charter needs both standing instructions and a notice.'))
            # required only stops NULL, and the url widget does not trim, so a charter
            # could name a blank authority in every prompt it governs
            if not (charter.reference_portal or '').strip():
                raise ValidationError(_('Name the portal this office treats as authoritative for legislation.'))


class LegalAIRequestCharter(models.Model):
    _inherit = 'legal.ai.request'

    charter_id = fields.Many2one(
        'legal.ai.charter', string='Charter Applied', readonly=True, copy=False,
        help="The standing instructions in force when this request was sent, kept so an old answer "
             "can be read against the rules it was produced under.")

    def _build_extra_system_context(self):
        """The office's standing instructions, ahead of the agent's own prompt."""
        self.ensure_one()
        charter = self.charter_id or self.env['legal.ai.charter']._for_company(self.company_id)
        if not charter:
            return ''
        body = charter.body or ''
        if charter.reference_portal:
            body = _(
                '%(body)s\n\nالمرجع الوحيد للأنظمة السعودية هو %(portal)s. لا تستند إلى نظام '
                'أو مادة من خارجه، ولا تنقل حكماً من ولاية قضائية أخرى. وأنت لا تستطيع فتح هذا '
                'الرابط، فهو للتحقق البشري؛ ولا تستشهد بمادة إلا إذا ورد نصها في المعطيات أو في '
                'المصادر المرفوعة إليك.',
                body=body, portal=charter.reference_portal)
        return body

    def _dispatch_to_provider(self, payload):
        self.ensure_one()
        charter = self.env['legal.ai.charter']._for_company(self.company_id)
        if charter:
            self.charter_id = charter
        return super()._dispatch_to_provider(payload)

    def _store_sanitized_response(self, response):
        """Append the office's notice to the answer itself."""
        self.ensure_one()
        charter = self.charter_id or self.env['legal.ai.charter']._for_company(self.company_id)
        notice = (charter.disclaimer or '').strip()
        if notice and response and notice not in response:
            response = f'{response}\n\n---\n\n**{notice}**'
        return super()._store_sanitized_response(response)

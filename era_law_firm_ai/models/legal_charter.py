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
    legislation_ids = fields.Many2many(
        'legal.legislation', string='Legislation Relied On',
        help="The statutes this office works from. Listing one here does not put its text in front "
             "of the agent -- for that the text must be attached to the agent as a source.")

    _one_active_per_company = models.Constraint(
        'UNIQUE(company_id, active)',
        'Only one charter can be active per company.')

    @api.model
    def _for_company(self, company):
        """The charter that governs this company, if any."""
        return self.sudo().search([
            '|', ('company_id', '=', company.id), ('company_id', '=', False),
        ], order='company_id desc, sequence', limit=1)

    @api.constrains('body', 'disclaimer')
    def _check_not_empty(self):
        for charter in self:
            if not (charter.body or '').strip() or not (charter.disclaimer or '').strip():
                raise ValidationError(_('A charter needs both standing instructions and a notice.'))


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
        return charter.body or ''

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

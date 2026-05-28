"""ERA SEO — wizard to launch an audit synchronously from the UI."""
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class EraSeoAuditWizard(models.TransientModel):
    _name = 'era.seo.audit.wizard'
    _description = 'SEO Audit Wizard'

    website_id = fields.Many2one(
        'website',
        string='Website',
        help='Scope the audit to one website. Empty = scan every website.',
    )

    def action_run(self):
        self.ensure_one()
        Run = self.env['era.seo.audit.run']
        run = Run.create({
            'website_id': self.website_id.id if self.website_id else False,
        })
        run._run_audit()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Audit Findings'),
            'res_model': 'era.seo.audit.run',
            'res_id': run.id,
            'view_mode': 'form',
            'target': 'current',
        }

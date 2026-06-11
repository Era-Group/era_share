import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class QuickTranslateWizard(models.TransientModel):
    _name = 'quick.translate.wizard'
    _description = 'Quick Translate Wizard'

    module_id = fields.Many2one(
        'ir.module.module',
        string='Module',
        required=True,
        domain=[('state', '=', 'installed')],
        help='Select the installed module whose translations you want to edit.',
    )
    source_lang_id = fields.Many2one(
        'res.lang',
        string='Source Language',
        required=True,
        default=lambda self: self.env.ref('base.lang_en', raise_if_not_found=False),
        help='Language of the original source text.',
    )
    target_lang_id = fields.Many2one(
        'res.lang',
        string='Target Language',
        required=True,
        help='The language you are translating into.',
    )
    auto_translate = fields.Boolean(
        string='Auto-Translate on Open',
        default=False,
        help='Automatically fill in all untranslated terms using Google Translate when the session opens.',
    )
    session_name = fields.Char(string='Session Name')

    @api.onchange('module_id', 'target_lang_id')
    def _onchange_build_name(self):
        if self.module_id and self.target_lang_id:
            self.session_name = '%s → %s' % (
                self.module_id.shortdesc or self.module_id.name,
                self.target_lang_id.name,
            )

    def action_open_session(self):
        self.ensure_one()
        session = self.env['translation.session'].create({
            'name': self.session_name or ('%s → %s' % (
                self.module_id.name, self.target_lang_id.code)),
            'module_id': self.module_id.id,
            'source_lang_id': self.source_lang_id.id,
            'target_lang_id': self.target_lang_id.id,
            'state': 'draft',
        })

        try:
            session.action_load_po()
        except UserError:
            raise

        if self.auto_translate and session.line_ids:
            try:
                session.action_auto_translate_all()
            except UserError as e:
                _logger.warning('Auto-translate on open failed: %s', e)

        return {
            'type': 'ir.actions.act_window',
            'name': _('Translation Session'),
            'res_model': 'translation.session',
            'res_id': session.id,
            'view_mode': 'form',
            'target': 'current',
        }

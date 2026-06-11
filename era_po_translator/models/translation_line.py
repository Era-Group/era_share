import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TranslationSessionLine(models.Model):
    _name = 'translation.session.line'
    _description = 'Translation Session Line'
    _order = 'sequence, id'

    session_id = fields.Many2one('translation.session', string='Session',
                                 required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string='Sequence', default=10)

    msgid = fields.Text(string='Source Text (msgid)', required=True)
    msgstr = fields.Text(string='Translation (msgstr)')
    comment = fields.Text(string='PO Comment')

    is_translated = fields.Boolean(string='Translated', compute='_compute_is_translated',
                                   store=True)

    @api.depends('msgstr')
    def _compute_is_translated(self):
        for rec in self:
            rec.is_translated = bool(rec.msgstr and rec.msgstr.strip())

    def action_suggest_translations(self):
        """Open wizard showing translation suggestions for this line."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Translation Suggestions'),
            'res_model': 'suggest.translation.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_line_id': self.id,
                'default_session_id': self.session_id.id,
                'default_source_text': self.msgid,
                'default_source_lang': self.session_id.source_lang,
                'default_target_lang_code': self.session_id.target_lang_id.code,
            },
        }

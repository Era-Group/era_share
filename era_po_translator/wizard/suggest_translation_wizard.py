import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Number of translation alternatives to fetch
MAX_SUGGESTIONS = 5


class SuggestTranslationWizard(models.TransientModel):
    _name = 'suggest.translation.wizard'
    _description = 'Translation Suggestions Wizard'

    line_id = fields.Many2one('translation.session.line', string='Term', required=True,
                              ondelete='cascade')
    session_id = fields.Many2one('translation.session', string='Session')
    source_text = fields.Text(string='Source Text', readonly=True)
    source_lang = fields.Char(string='Source Language Code')
    target_lang_code = fields.Char(string='Target Language Code')

    suggestion_ids = fields.One2many('suggest.translation.wizard.line', 'wizard_id',
                                     string='Suggestions')
    chosen_translation = fields.Text(string='Chosen Translation')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        return res

    def action_load_suggestions(self):
        """Fetch translation suggestions from Google Translate."""
        self.ensure_one()
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            raise UserError(_(
                'The deep_translator Python package is required.\n\n'
                'Install it with:  pip install deep-translator'
            ))

        if not self.source_text or not self.source_text.strip():
            raise UserError(_('Source text is empty.'))

        src = (self.source_lang or 'en').split('_')[0]
        tgt = (self.target_lang_code or 'ar').split('_')[0]

        suggestions = []
        try:
            translator = GoogleTranslator(source=src, target=tgt)
            main = translator.translate(self.source_text)
            if main:
                suggestions.append(main)
        except Exception as exc:
            raise UserError(_('Google Translate error: %s') % exc)

        # Try additional alternatives via different informal source codes
        alt_sources = ['auto']
        for alt_src in alt_sources:
            if len(suggestions) >= MAX_SUGGESTIONS:
                break
            try:
                translator2 = GoogleTranslator(source=alt_src, target=tgt)
                alt = translator2.translate(self.source_text)
                if alt and alt not in suggestions:
                    suggestions.append(alt)
            except Exception:
                pass

        # Clear existing and create new suggestion lines
        self.suggestion_ids.unlink()
        vals = [{'wizard_id': self.id, 'suggestion': s} for s in suggestions]
        if vals:
            self.env['suggest.translation.wizard.line'].create(vals)

        # Pre-select first suggestion
        if suggestions:
            self.chosen_translation = suggestions[0]

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Translation Suggestions'),
        }

    def action_apply(self):
        """Apply the chosen translation to the session line."""
        self.ensure_one()
        if not self.chosen_translation:
            raise UserError(_('Please select or enter a translation.'))
        self.line_id.msgstr = self.chosen_translation
        return {'type': 'ir.actions.act_window_close'}


class SuggestTranslationWizardLine(models.TransientModel):
    _name = 'suggest.translation.wizard.line'
    _description = 'Translation Suggestion'

    wizard_id = fields.Many2one('suggest.translation.wizard', required=True, ondelete='cascade')
    suggestion = fields.Text(string='Suggested Translation', readonly=True)

    def action_select(self):
        """Copy this suggestion into the wizard's chosen_translation field."""
        self.ensure_one()
        self.wizard_id.chosen_translation = self.suggestion
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'suggest.translation.wizard',
            'res_id': self.wizard_id.id,
            'view_mode': 'form',
            'target': 'new',
            'name': _('Translation Suggestions'),
        }

"""Deletion of anything attached to a legal file belongs to the legal manager.

Access rights already reserve unlink on the legal models themselves. What they do
not cover is the chatter: an activity or a logged note is deleted through
mail.activity / mail.message, whose rights are Odoo's, not ours. Without this a
lawyer could still erase the reminder they missed or the note they regret.

Marking an activity done is deliberately untouched -- Odoo archives it rather than
deleting it, so the history survives and anyone may still complete their own work.
"""

from odoo import _, models
from odoo.exceptions import AccessError

LEGAL_MODEL_PREFIX = 'legal.'


class LegalDeletionGuard(models.AbstractModel):
    _name = 'legal.deletion.guard'
    _description = 'Legal Deletion Guard'

    def _assert_legal_manager(self, what):
        if self.env.su or self.env.user.has_group('era_law_firm.group_legal_manager'):
            return
        raise AccessError(_(
            'Only a legal manager may delete %s. Everything attached to a legal file is '
            'part of its record: cancel it, mark it done or archive it instead.', what))

    def _is_legal_model(self, model_name):
        return bool(model_name) and model_name.startswith(LEGAL_MODEL_PREFIX)

    def _targets_a_live_legal_record(self, model_name, res_id):
        """True only while the legal record still exists.

        When the record itself has already gone, Odoo tidies up its orphaned
        activities and messages internally; blocking that would strand them.
        """
        if not self._is_legal_model(model_name) or not res_id:
            return False
        if model_name not in self.env:
            return False
        return bool(self.env[model_name].sudo().browse(res_id).exists())


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    def unlink(self):
        guard = self.env['legal.deletion.guard']
        if any(guard._targets_a_live_legal_record(a.res_model, a.res_id) for a in self):
            guard._assert_legal_manager(_('an activity on a legal record'))
        return super().unlink()


class MailMessage(models.Model):
    _inherit = 'mail.message'

    def unlink(self):
        guard = self.env['legal.deletion.guard']
        if any(guard._targets_a_live_legal_record(m.model, m.res_id) for m in self):
            guard._assert_legal_manager(_('a message or note on a legal record'))
        return super().unlink()

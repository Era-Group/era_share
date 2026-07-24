from odoo import models


class AccountReturn(models.Model):
    _inherit = 'account.return'

    def _update_translated_name(self):
        if self.env.context.get('update_returns_translation_lang') and not self.env.su:
            return super(AccountReturn, self.sudo())._update_translated_name()
        return super()._update_translated_name()

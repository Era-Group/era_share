# Part of Era Group custom addons.
from odoo import _, api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _init_store_data(self, store):
        """Expose the WAHA account name to the Discuss client so its dedicated
        sidebar category can be labelled after the account instead of a generic
        'WAHA'. With a single WAHA account (the usual case) the category shows its
        name; with none/many it falls back to 'WAHA' on the client."""
        super()._init_store_data(store)
        accounts = self.env['whatsapp.account'].sudo().search([('provider', '=', 'waha')])
        name = accounts.name if (len(accounts) == 1 and accounts.name) else _("WAHA")
        store.add_global_values(wahaCategoryName=name)

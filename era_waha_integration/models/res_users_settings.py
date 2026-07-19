# Part of Era Group custom addons.
from odoo import fields, models


class ResUsersSettings(models.Model):
    _inherit = 'res.users.settings'

    is_discuss_sidebar_category_waha_open = fields.Boolean(
        string='WAHA Category Open', default=True,
        help="If checked, the WAHA category is open in the discuss sidebar.")

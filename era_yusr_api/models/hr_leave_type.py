# -*- coding: utf-8 -*-
from odoo import fields, models


class HrLeaveType(models.Model):
    _inherit = 'hr.leave.type'

    yusr_visible = fields.Boolean(
        string='Show in Yusr Mobile App',
        default=True,
        help="When unchecked, this leave type is hidden from the Yusr "
             "mobile app's picker and balances screen. Use this to hide "
             "types seeded by localization modules (e.g. 'European Leave', "
             "'Pilgrimage Leave', 'Study Leave') that don't apply to "
             "your mobile workflow, without archiving the record for "
             "the rest of Odoo.",
    )

# -*- coding: utf-8 -*-
from odoo import fields, models


class VoipCall(models.Model):
    _inherit = 'voip.call'

    cs_timeline_synced = fields.Boolean(
        string='CS Timeline Synced', default=False, copy=False, index=True)

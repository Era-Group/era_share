# -*- coding: utf-8 -*-
from odoo import fields, models


class SurveyUserInput(models.Model):
    _inherit = 'survey.user_input'

    cs_timeline_synced = fields.Boolean(
        string='CS Timeline Synced', default=False, copy=False, index=True)

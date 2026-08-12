# -*- coding: utf-8 -*-
"""The two settings that only mean something when Project is installed.

They live here rather than in the base app so that a database without Project
is not offered a setting for a bucket task it can never create. The view adds
them into the base app's own Sembly settings block, so the user still sees one
coherent Sembly page.
"""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sembly_auto_create_meetings_task = fields.Boolean(
        string="إنشاء تاسك 'الاجتماعات' تلقائياً",
        config_parameter='sembly.auto_create_meetings_task',
        help="When a project matches but no task and nothing more specific "
             "does, file the meeting under a per-project bucket task, created "
             "once and reused.")
    sembly_meetings_task_name = fields.Char(
        string="اسم تاسك الاجتماعات",
        config_parameter='sembly.meetings_task_name')

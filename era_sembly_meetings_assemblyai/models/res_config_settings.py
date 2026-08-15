# -*- coding: utf-8 -*-
"""Settings for the paid AssemblyAI fallback channel."""
import os

from odoo import api, fields, models


API_KEY_PARAM = 'sembly.assemblyai_api_key'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sembly_assemblyai_enabled = fields.Boolean(
        string="تفعيل تفريغ AssemblyAI",
        config_parameter='sembly.assemblyai_enabled')
    sembly_assemblyai_api_key = fields.Char(
        string="مفتاح AssemblyAI API",
        help="Stored write-only. ASSEMBLYAI_API_KEY takes precedence when set.")
    sembly_assemblyai_key_is_set = fields.Boolean(
        string="المفتاح مُعرَّف", compute='_compute_assemblyai_key_state')
    sembly_assemblyai_key_from_env = fields.Boolean(
        string="المفتاح من البيئة", compute='_compute_assemblyai_key_state')
    sembly_assemblyai_region = fields.Selection(
        [('us', "الولايات المتحدة"), ('eu', "الاتحاد الأوروبي")],
        string="منطقة AssemblyAI", default='us',
        config_parameter='sembly.assemblyai_region')
    sembly_assemblyai_sembly_policy = fields.Selection(
        [('auto', "تلقائي"), ('always', "انتظر Sembly دائماً"),
         ('never', "Google فقط - ابدأ فوراً")],
        string="سياسة انتظار Sembly", default='auto',
        config_parameter='sembly.assemblyai_sembly_policy')
    sembly_assemblyai_wait_hours = fields.Integer(
        string="مهلة Sembly (ساعات)",
        config_parameter='sembly.assemblyai_wait_hours')
    sembly_assemblyai_monthly_hours = fields.Integer(
        string="السقف الشهري (ساعات)",
        config_parameter='sembly.assemblyai_monthly_hours')
    sembly_assemblyai_month_usage = fields.Char(
        string="استخدام الشهر", compute='_compute_assemblyai_usage')

    @api.model
    def _assemblyai_key_mask(self, raw):
        return ('•' * 12 + (raw[-4:] if len(raw) >= 4 else '')) if raw else ''

    def _compute_assemblyai_key_state(self):
        env_key = bool((os.environ.get('ASSEMBLYAI_API_KEY') or '').strip())
        db_key = bool(self.env['ir.config_parameter'].sudo().get_param(
            API_KEY_PARAM))
        for record in self:
            record.sembly_assemblyai_key_is_set = env_key or db_key
            record.sembly_assemblyai_key_from_env = env_key

    def _compute_assemblyai_usage(self):
        usage = self.env['sembly.meeting'].sudo()._assemblyai_month_usage_seconds()
        text = "%.1f ساعة" % (usage / 3600.0)
        for record in self:
            record.sembly_assemblyai_month_usage = text

    @api.model
    def get_values(self):
        values = super().get_values()
        raw = self.env['ir.config_parameter'].sudo().get_param(API_KEY_PARAM) or ''
        values['sembly_assemblyai_api_key'] = self._assemblyai_key_mask(raw)
        return values

    def set_values(self):
        super().set_values()
        icp = self.env['ir.config_parameter'].sudo()
        current = icp.get_param(API_KEY_PARAM) or ''
        submitted = (self.sembly_assemblyai_api_key or '').strip()
        if submitted and submitted != self._assemblyai_key_mask(current):
            icp.set_param(API_KEY_PARAM, submitted)
        elif not submitted and current:
            icp.set_param(API_KEY_PARAM, '')

# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmRealtimeCallSummaryPlayWizard(models.TransientModel):
    _name = "crm.realtime_call_summary_play_wizard"
    _description = "Realtime Call Recording Player"

    attachment_id = fields.Many2one("ir.attachment", readonly=True)
    audio = fields.Binary(related="attachment_id.datas", readonly=True)
    mimetype = fields.Char(related="attachment_id.mimetype", readonly=True)
    filename = fields.Char(related="attachment_id.name", readonly=True)

    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        attachment_id = result.get('attachment_id')
        if attachment_id:
            # Force load the datas by reading the attachment
            attachment = self.env["ir.attachment"].browse(attachment_id).exists()
            if attachment:
                result['audio'] = attachment.datas
                result['mimetype'] = attachment.mimetype
                result['filename'] = attachment.name
        return result

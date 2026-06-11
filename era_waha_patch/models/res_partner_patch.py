from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Rename the count field label to avoid duplicate "WhatsApp Messages" label warning.
    # The vendor module (sadeem_waha_whatsapp) uses the same label for both the One2many
    # and the computed Integer field.
    waha_whatsapp_message_count = fields.Integer(
        "WhatsApp Message Count",
        compute="_compute_waha_whatsapp_message_count",
    )

import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class WhatsAppMessageDedup(models.Model):
    _inherit = "sadeem.waha.whatsapp.message"

    @api.model_create_multi
    def create(self, vals_list):
        deduped = []
        existing = []
        for vals in vals_list:
            waha_id = vals.get("waha_message_id")
            if waha_id:
                dup = self.sudo().search(
                    [("waha_message_id", "=", waha_id)], limit=1
                )
                if dup:
                    _logger.debug("Skipping duplicate waha message %s", waha_id)
                    existing.append(dup)
                    continue
            deduped.append(vals)
        created = super().create(deduped) if deduped else self.browse()
        for rec in existing:
            created |= rec
        return created

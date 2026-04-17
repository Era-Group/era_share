import logging
from odoo import models, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

LOCK_NS = 0x57414D53  # 'WAMS'


class WhatsAppMessageDedup(models.Model):
    _inherit = "sadeem.waha.whatsapp.message"

    def init(self):
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
                sadeem_waha_msg_waha_id_uniq
            ON sadeem_waha_whatsapp_message (waha_message_id)
            WHERE waha_message_id IS NOT NULL
              AND waha_message_id != ''
              AND waha_message_id != 'false'
        """)

    @api.model_create_multi
    def create(self, vals_list):
        deduped = []
        for vals in vals_list:
            waha_id = vals.get("waha_message_id")
            if not waha_id or waha_id == "false":
                deduped.append(vals)
                continue
            lock_key = hash(waha_id) & 0x7FFFFFFF
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NS, lock_key)
            )
            dup = self.sudo().search(
                [("waha_message_id", "=", waha_id)], limit=1
            )
            if dup:
                raise UserError("Duplicate waha message %s" % waha_id)
            deduped.append(vals)
        return super().create(deduped)

import logging
import re
import zlib
from odoo import models, api

_logger = logging.getLogger(__name__)

LOCK_NS = 0x57414D53  # 'WAMS'


def _stable_hash(s):
    return zlib.crc32(s.encode("utf-8")) & 0x7FFFFFFF


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
        skipped = self.browse()
        for vals in vals_list:
            waha_id = vals.get("waha_message_id")
            if not waha_id or waha_id == "false":
                deduped.append(vals)
                continue
            lock_key = _stable_hash(waha_id)
            self.env.cr.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NS, lock_key)
            )
            dup = self.sudo().search(
                [("waha_message_id", "=", waha_id)], limit=1
            )
            if dup:
                _logger.debug("Skipping duplicate waha message %s", waha_id)
                skipped |= dup
                continue
            deduped.append(vals)
        created = super().create(deduped) if deduped else self.browse()
        return created | skipped

    def get_formview_action(self, access_uid=None):
        self.ensure_one()
        phone = re.sub(r"\D", "", (self.phone_number or "").lstrip("+"))
        return {
            "type": "ir.actions.client",
            "tag": "era_waha_open_chat",
            "params": {
                "chat_id": "%s@c.us" % phone,
                "session_id": self.session_id.id,
                "phone_number": self.phone_number or "",
                "partner_id": self.partner_id.id or False,
                "partner_name": self.partner_id.name or self.phone_number or "",
            },
        }

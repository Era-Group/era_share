import logging
import re
from odoo import models, api

_logger = logging.getLogger(__name__)


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
        if len(vals_list) != 1:
            return super().create(vals_list)
        vals = vals_list[0]
        waha_id = vals.get("waha_message_id")
        if not waha_id or waha_id == "false":
            return super().create(vals_list)

        existing = self.sudo().search([("waha_message_id", "=", waha_id)], limit=1)
        if existing:
            return existing

        cr = self.env.cr
        now = vals.get("create_date") or "now()"
        cr.execute("""
            INSERT INTO sadeem_waha_whatsapp_message
                (session_id, partner_id, phone_number, chat_id, direction,
                 message_type, status, text, waha_message_id, attachment_id,
                 create_date, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (waha_message_id)
                WHERE waha_message_id IS NOT NULL
                  AND waha_message_id != ''
                  AND waha_message_id != 'false'
            DO NOTHING
            RETURNING id
        """, (
            vals.get("session_id"),
            vals.get("partner_id") or None,
            vals.get("phone_number"),
            vals.get("chat_id"),
            vals.get("direction"),
            vals.get("message_type", "text"),
            vals.get("status", "delivered"),
            vals.get("text"),
            waha_id,
            vals.get("attachment_id") or None,
        ))
        row = cr.fetchone()
        if row:
            self.env.cache.invalidate()
            return self.browse(row[0])

        existing = self.sudo().search([("waha_message_id", "=", waha_id)], limit=1)
        if existing:
            return existing
        return super().create(vals_list)

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

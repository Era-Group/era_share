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

        partner_id = vals.get("partner_id") or None
        phone = vals.get("phone_number") or ""
        text = vals.get("text") or ""
        mtype = vals.get("message_type", "text")
        if partner_id:
            partner = self.env["res.partner"].sudo().browse(partner_id)
            display = partner.name or phone
        else:
            display = phone
        if text:
            preview = text[:50] + "..." if len(text) > 50 else text
            computed_name = "%s: %s" % (display, preview)
        else:
            computed_name = "%s: [%s]" % (display, mtype)

        cr = self.env.cr
        new_id = None
        try:
            with cr.savepoint():
                cr.execute("""
                    INSERT INTO sadeem_waha_whatsapp_message
                        (name, session_id, partner_id, phone_number, chat_id, direction,
                         message_type, status, text, waha_message_id, attachment_id,
                         create_date, write_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (waha_message_id)
                        WHERE waha_message_id IS NOT NULL
                          AND waha_message_id != ''
                          AND waha_message_id != 'false'
                    DO NOTHING
                    RETURNING id
                """, (
                    computed_name,
                    vals.get("session_id"),
                    partner_id,
                    phone,
                    vals.get("chat_id"),
                    vals.get("direction"),
                    mtype,
                    vals.get("status", "delivered"),
                    text or None,
                    waha_id,
                    vals.get("attachment_id") or None,
                ))
                row = cr.fetchone()
                if row:
                    new_id = row[0]
        except Exception:
            pass

        if new_id:
            self.env.cache.invalidate()
            return self.browse(new_id)

        existing = self.sudo().search([("waha_message_id", "=", waha_id)], limit=1)
        return existing or self.browse()

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

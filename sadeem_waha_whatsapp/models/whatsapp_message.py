# -*- coding: utf-8 -*-
from odoo import models, fields, api
import base64
import logging
import re

_logger = logging.getLogger(__name__)


class SadeemWahaWhatsAppMessage(models.Model):
    _name = 'sadeem.waha.whatsapp.message'
    _description = 'WhatsApp Message'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Subject', compute='_compute_name', store=True)
    session_id = fields.Many2one('sadeem.waha.session', 'Session', required=True)
    partner_id = fields.Many2one('res.partner', 'Contact')
    phone_number = fields.Char('Phone Number', required=True)
    chat_id = fields.Char('Chat ID', required=True)

    message_type = fields.Selection([
        ('text', 'Text'),
        ('chat', 'Chat'),
        ('image', 'Image'),
        ('document', 'Document'),
        ('voice', 'Voice'),
        ('video', 'Video'),
        ('audio', 'Audio'),
        ('sticker', 'Sticker'),
        ('location', 'Location'),
        ('notification', 'Notification'),
    ], string='Type', default='text')

    direction = fields.Selection([
        ('outgoing', 'Outgoing'),
        ('incoming', 'Incoming')
    ], string='Direction', required=True)

    text = fields.Text('Message Text')
    attachment_id = fields.Many2one('ir.attachment', 'Attachment')

    status = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
        ('failed', 'Failed')
    ], string='Status', default='draft')

    waha_message_id = fields.Char('WAHA Message ID', index=True)
    # Current reaction emoji on this message ('' = none). NOWEB doesn't return
    # reactions in the message list, so we persist them from the live webhook.
    reaction = fields.Char('Reaction')
    error_message = fields.Text('Error Message')

    company_id = fields.Many2one('res.company', 'Company', default=lambda self: self.env.company)

    attachment_mimetype = fields.Char(
        string='MIME Type', compute='_compute_attachment_info', store=False)
    attachment_file_size = fields.Char(
        string='File Size', compute='_compute_attachment_info', store=False)
    attachment_download_url = fields.Char(
        string='Download URL', compute='_compute_attachment_info', store=False)

    @api.depends('attachment_id')
    def _compute_attachment_info(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for rec in self:
            att = rec.attachment_id
            if att:
                rec.attachment_mimetype = att.mimetype or ''
                size = att.file_size or 0
                if size >= 1024 * 1024:
                    rec.attachment_file_size = f"{size / (1024 * 1024):.1f} MB"
                elif size >= 1024:
                    rec.attachment_file_size = f"{size / 1024:.1f} KB"
                else:
                    rec.attachment_file_size = f"{size} B"
                rec.attachment_download_url = (
                    f"{base_url}/web/content/{att.id}/{att.name}?download=true"
                )
            else:
                rec.attachment_mimetype = ''
                rec.attachment_file_size = ''
                rec.attachment_download_url = ''

    @api.depends('partner_id', 'phone_number', 'text')
    def _compute_name(self):
        for record in self:
            if record.partner_id:
                name = record.partner_id.name
            else:
                name = record.phone_number

            if record.text:
                text_preview = record.text[:50] + '...' if len(record.text) > 50 else record.text
                record.name = f"{name}: {text_preview}"
            else:
                record.name = f"{name}: [{record.message_type}]"

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
        # WAHA delivers the same message on several workers at once; their
        # concurrent INSERTs of the same waha_message_id raise "could not
        # serialize access" instead of cleanly hitting ON CONFLICT. A txn-level
        # advisory lock keyed on the id makes duplicates queue, so only the
        # first inserts and the rest fall through to the search below.
        # ponytail: hashtext gives a 32-bit key — collisions just serialize two
        # unrelated messages briefly, harmless.
        cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (waha_id,))
        existing = self.sudo().search([("waha_message_id", "=", waha_id)], limit=1)
        if existing:
            return existing

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
            "tag": "sadeem_waha_open_chat",
            "params": {
                "chat_id": "%s@c.us" % phone,
                "session_id": self.session_id.id,
                "phone_number": self.phone_number or "",
                "partner_id": self.partner_id.id or False,
                "partner_name": self.partner_id.name or self.phone_number or "",
            },
        }

    def action_send(self):
        """Send the message"""
        self.ensure_one()

        if self.direction != 'outgoing':
            return

        try:
            if self.message_type == 'text':
                result = self.session_id.send_message(self.chat_id, self.text)
            elif self.message_type == 'voice' and self.attachment_id:
                voice_data = base64.b64encode(self.attachment_id.raw).decode()
                result = self.session_id.send_voice_message(self.chat_id, voice_data, self.attachment_id.name)
            elif self.attachment_id:
                file_data = base64.b64encode(self.attachment_id.raw).decode()
                result = self.session_id.send_message(
                    self.chat_id,
                    self.text,
                    file_data,
                    self.attachment_id.name,
                    self.attachment_id.mimetype
                )

            if result:
                self.status = 'sent'
                self.waha_message_id = result.get('id')

        except Exception as e:
            self.status = 'failed'
            self.error_message = str(e)

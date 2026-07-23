# Part of Era Group custom addons.
import mimetypes

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import html2plaintext


class WhatsappComposer(models.TransientModel):
    _inherit = 'whatsapp.composer'

    # Account selector: pick which whatsapp.account (Meta or WAHA) to send from.
    wa_account_id = fields.Many2one(
        'whatsapp.account', string="Send From",
        compute='_compute_wa_account_id', store=True, readonly=False)
    is_waha = fields.Boolean(compute='_compute_is_waha')

    # WAHA free-text mode (no template): a rich message body (converted to WhatsApp
    # formatting on send) + an optional image/file.
    waha_body = fields.Html(string="Message", sanitize_style=True)
    waha_attachment = fields.Binary(string="Image / File")
    waha_attachment_name = fields.Char(string="Attachment Filename")

    # ------------------------------------------------------------
    # DEFAULTS / COMPUTES
    # ------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        # A WAHA account needs no template; tell the base default_get not to abort
        # with "no approved templates" when one is available.
        waha_account = self.env['whatsapp.account'].search(
            [('provider', '=', 'waha')], order='id', limit=1)
        result = super(
            WhatsappComposer,
            self.with_context(era_waha_available=bool(waha_account)),
        ).default_get(fields_list)
        if not result.get('wa_account_id'):
            if result.get('wa_template_id'):
                result['wa_account_id'] = self.env['whatsapp.template'].browse(
                    result['wa_template_id']).wa_account_id.id
            elif waha_account:
                result['wa_account_id'] = waha_account.id
        # Default the phone from the record when opening in WAHA (no-template) mode.
        account = self.env['whatsapp.account'].browse(result.get('wa_account_id'))
        if (account.provider == 'waha' and not result.get('phone')
                and not result.get('batch_mode') and result.get('res_ids') and result.get('res_model')):
            record = self.env[result['res_model']].browse(result['res_ids'])[:1]
            if record:
                result['phone'] = self._waha_number_for_record(record)
        return result

    def _raise_no_template_error(self, res_model):
        # In WAHA mode there is no template requirement.
        if self.env.context.get('era_waha_available'):
            return
        return super()._raise_no_template_error(res_model)

    @api.depends('wa_template_id')
    def _compute_wa_account_id(self):
        for rec in self:
            if rec.wa_template_id:
                rec.wa_account_id = rec.wa_template_id.wa_account_id
            elif not rec.wa_account_id:
                rec.wa_account_id = self.env['whatsapp.account'].search(
                    [('provider', '=', 'waha')], order='id', limit=1
                ) or self.env['whatsapp.account'].search([], order='id', limit=1)

    @api.depends('wa_account_id')
    def _compute_is_waha(self):
        for rec in self:
            rec.is_waha = rec.wa_account_id.provider == 'waha'

    @api.onchange('wa_account_id')
    def _onchange_wa_account_id(self):
        # A template belongs to a Meta account; clear it when switching to WAHA.
        if self.wa_account_id.provider == 'waha':
            self.wa_template_id = False

    # ------------------------------------------------------------
    # SEND
    # ------------------------------------------------------------
    def action_send_whatsapp_template(self):
        self.ensure_one()
        if self.is_waha:
            return self._send_waha_message()
        return super().action_send_whatsapp_template()

    def _send_waha_message(self):
        """Send a free-text/image WAHA message by routing it THROUGH the Discuss
        channel of the recipient's number, so it appears in the conversation (like a
        reply typed in Discuss) and is delivered via WAHA. A note linking to the
        conversation is logged on the source record."""
        self.ensure_one()
        if not html2plaintext(self.waha_body or '').strip() and not self.waha_attachment:
            raise ValidationError(_("Enter a message or attach a file."))
        records = self._get_active_records()
        body = self.waha_body or ''  # already HTML; converted to WhatsApp format on send
        author = self.env.user.partner_id
        for rec in records:
            number = self.phone if not self.batch_mode else self._waha_number_for_record(rec)
            formatted = rec._whatsapp_phone_format(
                number=number, raise_on_format_error=not self.batch_mode) if number else False
            if not formatted:
                if not self.batch_mode:
                    raise ValidationError(_("Invalid phone number."))
                continue
            # Account-protection (anti-ban): verify BEFORE sending anything. If blocked,
            # the error is shown and this composer stays OPEN with the text intact, so the
            # user can switch "Send From" to the official (Meta) WhatsApp — or copy the
            # text and use another channel — without losing what they typed.
            self.wa_account_id._waha_check_send_allowed(formatted, self.env.user)
            sender_name = False
            if 'partner_id' in rec._fields and rec.partner_id:
                sender_name = rec.partner_id.name
            elif rec.display_name:
                sender_name = rec.display_name
            channel = self.wa_account_id._waha_send_via_channel(
                formatted, body,
                attachment_ids=self._waha_prepare_attachment_ids(),
                sender_name=sender_name, author=author)
            if not channel:
                continue
            # Trace the interaction on the source record (unless it is the channel itself).
            if self.res_model != 'discuss.channel' and hasattr(rec, 'message_post'):
                url = '%s/odoo/discuss.channel/%s' % (self.get_base_url(), channel.id)
                rec.message_post(
                    body=Markup('<p>%s <a href="%s">%s</a></p>') % (
                        _("WhatsApp message sent —"), url, channel.display_name),
                    subtype_xmlid='mail.mt_note',
                )
        return {'type': 'ir.actions.act_window_close'}

    # ------------------------------------------------------------
    # TOOLS
    # ------------------------------------------------------------
    def _waha_prepare_attachment_ids(self):
        self.ensure_one()
        if not self.waha_attachment:
            return []
        name = self.waha_attachment_name or 'attachment'
        attachment = self.env['ir.attachment'].create({
            'name': name,
            'datas': self.waha_attachment,
            'mimetype': mimetypes.guess_type(name)[0] or 'application/octet-stream',
        })
        return [attachment.id]

    @api.model
    def _waha_number_for_record(self, record):
        if hasattr(record, '_phone_get_number_fields'):
            for fname in record._phone_get_number_fields():
                if fname in record._fields and record[fname]:
                    return record[fname]
        for fname in ('mobile', 'phone'):
            if fname in record._fields and record[fname]:
                return record[fname]
        return False

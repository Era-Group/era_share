# Part of Era Group custom addons.
from lxml import html as lxml_html
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import html2plaintext


class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    wa_account_id = fields.Many2one(
        'whatsapp.account', string="WhatsApp Account",
        domain=[('provider', '=', 'waha')],
        compute='_compute_wa_account_id', store=True, readonly=False)
    waha_available = fields.Boolean(compute='_compute_waha_available')

    @api.depends('model')
    def _compute_wa_account_id(self):
        default = self.env['whatsapp.account'].search(
            [('provider', '=', 'waha')], order='id', limit=1)
        for rec in self:
            if not rec.wa_account_id:
                rec.wa_account_id = default

    def _compute_waha_available(self):
        available = bool(self.env['whatsapp.account'].search_count(
            [('provider', '=', 'waha')]))
        for rec in self:
            rec.waha_available = available

    def action_send_waha_whatsapp(self):
        """Send the composed body to the recipients' WhatsApp numbers through WAHA,
        routed through each recipient's Discuss channel (so it shows in the
        conversation). Same free-text format as the WhatsApp composer."""
        self.ensure_one()
        account = self.wa_account_id
        if not account or account.provider != 'waha':
            raise ValidationError(_("Select a WAHA WhatsApp account."))
        if not self.body or not html2plaintext(self.body).strip():
            raise ValidationError(_("The message body is empty."))
        body = self._waha_strip_signature(self.body)
        if not html2plaintext(body).strip() and not self.attachment_ids:
            raise ValidationError(_("The message is empty after removing the signature."))

        recipients = self.partner_ids
        if not recipients and self.model and self.res_ids:
            records = self.env[self.model].browse(self._evaluate_res_ids())
            partners_by_rec = records._mail_get_partners()
            recipients = self.env['res.partner'].union(*partners_by_rec.values()) \
                if partners_by_rec else self.env['res.partner']
        recipients = recipients.filtered(lambda p: self._waha_partner_number(p))
        if not recipients:
            raise ValidationError(_("No recipient has a phone number to reach on WhatsApp."))

        author = self.env.user.partner_id
        multi = len(recipients) > 1
        for partner in recipients:
            number = self._waha_partner_number(partner)
            formatted = partner._whatsapp_phone_format(number=number) or number
            # Account-protection (anti-ban): verify BEFORE sending. If blocked, the error
            # is shown and this dialog stays OPEN with the text intact — the user can send
            # via email or the official WhatsApp instead, without retyping.
            account._waha_check_send_allowed(formatted, self.env.user)
            attachments = self.attachment_ids
            if multi and attachments:
                # Give each recipient its own attachment copies (an ir.attachment
                # belongs to a single message/record).
                attachments = self.env['ir.attachment'].concat(
                    *[att.copy() for att in attachments])
            account._waha_send_via_channel(
                formatted, body, attachment_ids=attachments.ids,
                sender_name=partner.name, author=author)
        return {'type': 'ir.actions.act_window_close'}

    @api.model
    def _waha_partner_number(self, partner):
        for fname in ('mobile', 'phone'):
            if fname in partner._fields and partner[fname]:
                return partner[fname]
        return False

    @api.model
    def _waha_strip_signature(self, body):
        """Drop the email signature block from a composed body before sending over
        WhatsApp: cut from the first '--' separator or Odoo's data-o-mail-quote
        marker (which wraps the signature) onwards."""
        if not body:
            return body
        try:
            frag = lxml_html.fragment_fromstring(body, create_parent='o-root')
        except Exception:
            return body
        children = list(frag)
        cut = None
        for i, el in enumerate(children):
            if (el.text_content() or '').strip().rstrip() in ('--', '—'):
                cut = i
                break
            if el.get('data-o-mail-quote') or el.xpath('.//*[@data-o-mail-quote]'):
                cut = i
                break
        if cut is not None:
            for el in children[cut:]:
                frag.remove(el)
        # Return Markup so message_post treats it as HTML (a plain str would be
        # escaped, and WhatsApp would then receive the literal HTML tags).
        return Markup((frag.text or '') + ''.join(
            lxml_html.tostring(c, encoding='unicode') for c in frag))

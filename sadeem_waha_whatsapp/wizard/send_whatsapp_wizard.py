from . import send_whatsapp_wizard

# wizard/send_whatsapp_wizard.py
from odoo import models, fields, api, _
from odoo.exceptions import UserError
import re

class SadeemWahaSendWhatsAppWizard(models.TransientModel):
    _name = 'sadeem.waha.send.whatsapp.wizard'
    _description = 'Send WhatsApp Message Wizard'

    partner_id = fields.Many2one('res.partner', 'Contact')
    phone_number = fields.Char('Phone Number', required=True)
    session_id = fields.Many2one('sadeem.waha.session', 'WhatsApp Session', required=True)

    message_text = fields.Text('Message', required=False)
    message_type = fields.Selection([
        ('text', 'Text Only'),
        ('with_file', 'With File'),
        ('voice', 'Voice Message')
    ], string='Message Type', default='text')

    attachment_id = fields.Many2one('ir.attachment', 'Attachment')
    file_upload = fields.Binary('File')
    file_upload_name = fields.Char('File Name')
    voice_file = fields.Binary('Voice File')
    voice_filename = fields.Char('Voice Filename')

    template_id = fields.Many2one('sadeem.waha.whatsapp.template', 'Use Template')

    # For context from record
    res_model = fields.Char('Resource Model')
    res_id = fields.Integer('Resource ID')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        # Set default session from company
        company = self.env.company
        if company.default_whatsapp_session_id:
            res['session_id'] = company.default_whatsapp_session_id.id

        # Get context values
        context = self.env.context
        if context.get('active_model'):
            res['res_model'] = context['active_model']
        if context.get('active_id'):
            res['res_id'] = context['active_id']

            # If coming from a record, try to get phone number and fill template
            if res.get('res_model'):
                record = self.env[res['res_model']].browse(res['res_id'])

                # Try to find a suitable template for this model
                template = self.env['sadeem.waha.whatsapp.template'].search([
                    ('model', '=', res['res_model'])
                ], limit=1)

                if template:
                    res['template_id'] = template.id
                    # Fill message with values from record
                    res['message_text'] = template.get_filled_message(record)

                # Get phone number from record
                if hasattr(record, 'waha_whatsapp_number') and record.waha_whatsapp_number:
                    res['phone_number'] = record.waha_whatsapp_number
                elif hasattr(record, 'mobile') and record.mobile:
                    res['phone_number'] = re.sub(r'[\s\-\(\)\.]', '', record.mobile)
                elif hasattr(record, 'phone') and record.phone:
                    res['phone_number'] = re.sub(r'[\s\-\(\)\.]', '', record.phone)

        return res

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id:
            # Get the active record if available
            record = None
            if self.res_model and self.res_id:
                record = self.env[self.res_model].browse(self.res_id)

            # Fill message with values from record (or demo values if no record)
            self.message_text = self.template_id.get_filled_message(record)

            if self.template_id.attachment_id:
                self.attachment_id = self.template_id.attachment_id
                self.message_type = 'with_file'

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if self.partner_id:
            # Get WhatsApp number from partner (computed from mobile/phone)
            if self.partner_id.waha_whatsapp_number:
                self.phone_number = self.partner_id.waha_whatsapp_number
            elif self.partner_id.mobile:
                # Fallback to mobile if computed field not yet available
                self.phone_number = re.sub(r'[\s\-\(\)\.]', '', self.partner_id.mobile)
            elif self.partner_id.phone:
                # Fallback to phone if no mobile
                self.phone_number = re.sub(r'[\s\-\(\)\.]', '', self.partner_id.phone)
            else:
                self.phone_number = ''

    def _prepare_chat_id(self):
        """Prepare chat ID for WhatsApp"""
        phone = self.phone_number.strip()

        # Validate phone number format
        if not phone:
            raise UserError(_("Phone number is required"))

        # Phone must start with + for international format
        if not phone.startswith('+'):
            raise UserError(_(
                "Phone number must be in international format starting with '+'\n"
                "Example: +201234567890\n"
                "Current: %s"
            ) % phone)

        # Remove + sign
        phone = phone[1:]

        # Remove any non-digit characters
        phone = ''.join(filter(str.isdigit, phone))

        # Validate minimum length (country code + number)
        if len(phone) < 10:
            raise UserError(_(
                "Phone number seems too short. Please use international format.\n"
                "Example: +201234567890"
            ))

        return f"{phone}@c.us"

    def action_send(self):
        """Send the WhatsApp message"""
        self.ensure_one()

        if not self.session_id:
            raise UserError(_("Please select a WhatsApp session"))

        if self.session_id.status != 'working':
            raise UserError(_("WhatsApp session is not active. Please check the session status."))

        # Validate that at least message text or attachment is provided
        if not self.message_text and not self.file_upload and not self.voice_file:
            raise UserError(_("Please enter a message text or attach a file"))

        chat_id = self._prepare_chat_id()

        try:
            # Create message record
            message_vals = {
                'session_id': self.session_id.id,
                'partner_id': self.partner_id.id if self.partner_id else False,
                'phone_number': self.phone_number,
                'chat_id': chat_id,
                'text': self.message_text,
                'direction': 'outgoing',
                'message_type': self.message_type,
            }

            if self.message_type == 'with_file' and self.file_upload:
                import mimetypes
                mimetype = mimetypes.guess_type(self.file_upload_name or '')[0] or 'application/octet-stream'
                attachment = self.env['ir.attachment'].create({
                    'name': self.file_upload_name or 'file',
                    'datas': self.file_upload,
                    'mimetype': mimetype,
                })
                message_vals['attachment_id'] = attachment.id
                message_vals['message_type'] = self._get_file_type(mimetype)
            elif self.message_type == 'voice' and self.voice_file:
                # Create attachment for voice file
                attachment = self.env['ir.attachment'].create({
                    'name': self.voice_filename or 'voice_message.ogg',
                    'raw': self.voice_file,
                    'mimetype': 'audio/ogg',
                })
                message_vals['attachment_id'] = attachment.id
                message_vals['message_type'] = 'voice'

            message = self.env['sadeem.waha.whatsapp.message'].create(message_vals)

            # Send the message
            message.action_send()

            if message.status == 'sent':
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'message': _('WhatsApp message sent successfully!'),
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise UserError(_("Failed to send message: %s") % (message.error_message or 'Unknown error'))

        except Exception as e:
            raise UserError(_("Failed to send WhatsApp message: %s") % str(e))

    def _get_file_type(self, mimetype):
        """Determine file type based on mimetype"""
        if not mimetype:
            return 'document'

        if mimetype.startswith('image/'):
            return 'image'
        elif mimetype.startswith('video/'):
            return 'video'
        elif mimetype.startswith('audio/'):
            return 'voice'
        else:
            return 'document'

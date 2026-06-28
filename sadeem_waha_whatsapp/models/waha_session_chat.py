from odoo import models


class SadeemWahaSession(models.Model):
    _inherit = 'sadeem.waha.session'

    def action_open_waha_chat(self):
        """Open the WhatsApp-Web-style chat client action for this session."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'sadeem_waha_chat.float_open',
            'name': 'WhatsApp Chat',
            'target': 'new',
            'context': {
                'session_id': self.id,
                'session_name': self.name,
            },
        }

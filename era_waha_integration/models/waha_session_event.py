# Part of Era Group custom addons.
from odoo import api, fields, models


class WahaSessionEvent(models.Model):
    """Timestamped history of every WAHA session status transition.

    `whatsapp.account.waha_flap_count` is a bare counter that the daily cron resets, so
    once a disconnection storm is over there is nothing left to diagnose it with. This
    log keeps the transitions themselves, which is what tells a one-off blip apart from
    a reconnect loop (the pattern WhatsApp reads as an unstable unofficial client).
    """
    _name = 'whatsapp.waha.session.event'
    _description = 'WAHA Session Status Event'
    _order = 'id desc'
    _rec_name = 'status'

    account_id = fields.Many2one(
        'whatsapp.account', string='Account', required=True, ondelete='cascade', index=True)
    status = fields.Char(string='Status', required=True)
    previous_status = fields.Char(string='Previous Status')
    source = fields.Selection(
        [('webhook', 'Webhook'), ('probe', 'Status probe'), ('manual', 'Manual action')],
        string='Source', default='probe', required=True)
    note = fields.Char(string='Note')

    @api.model
    def _gc_session_events(self, days=90):
        """Drop events older than `days` — this is diagnostics, not a business record."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.sudo().search([('create_date', '<', limit)]).unlink()
        return True

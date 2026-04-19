import logging
from odoo import _, api, models
from odoo.addons.sadeem_waha_whatsapp.models.waha_session import SadeemWahaSession

_logger = logging.getLogger(__name__)

LOCK_NAMESPACE = 0x57414841  # 'WAHA'


class SadeemWahaSessionPatch(models.Model):
    _inherit = "sadeem.waha.session"

    @api.model_create_multi
    def create(self, vals_list):
        # Upstream create is declared with @api.model + single-dict signature,
        # which breaks under Odoo 18's vals_list dispatch. Skip it via MRO and
        # replicate its side effects (session_id default, log, message_post).
        for vals in vals_list:
            if not vals.get('session_id'):
                vals['session_id'] = vals.get('name', '').lower().replace(' ', '_')
        records = super(SadeemWahaSession, self).create(vals_list)
        for record in records:
            _logger.info(
                "WhatsApp Session created: %s (ID: %s)", record.name, record.id
            )
            record.message_post(
                body=_("WhatsApp session '%s' has been created successfully.") % record.name,
                subject=_("Session Created"),
            )
        return records

    def write(self, vals):
        if "status" in vals:
            for rec in self:
                self.env.cr.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (LOCK_NAMESPACE, rec.id),
                )
        return super().write(vals)

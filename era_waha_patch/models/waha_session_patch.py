import logging
from odoo import api, models

_logger = logging.getLogger(__name__)

LOCK_NAMESPACE = 0x57414841  # 'WAHA'


class SadeemWahaSessionPatch(models.Model):
    _inherit = "sadeem.waha.session"

    @api.model_create_multi
    def create(self, vals_list):
        # Upstream create is declared with @api.model + single-dict signature,
        # which breaks under Odoo 18's vals_list dispatch. Normalize here and
        # delegate one-by-one so the upstream body runs with a dict.
        records = self.browse()
        for vals in vals_list:
            records |= super().create(vals)
        return records

    def write(self, vals):
        if "status" in vals:
            for rec in self:
                self.env.cr.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (LOCK_NAMESPACE, rec.id),
                )
        return super().write(vals)

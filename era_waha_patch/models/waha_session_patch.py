import logging
from odoo import models

_logger = logging.getLogger(__name__)

LOCK_NAMESPACE = 0x57414841  # 'WAHA'


class SadeemWahaSessionPatch(models.Model):
    _inherit = "sadeem.waha.session"

    def write(self, vals):
        if "status" in vals:
            for rec in self:
                self.env.cr.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (LOCK_NAMESPACE, rec.id),
                )
        return super().write(vals)

# -*- coding: utf-8 -*-
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def _set_default_eraspy_base_url(self):
        """Set the default EraSpy Base URL on module installation/upgrade."""
        ICP = self.env['ir.config_parameter'].sudo()
        current_url = ICP.get_param('era_spy.base_url')
        if not current_url:
            ICP.set_param('era_spy.base_url', 'https://spy.era.net.sa/api')
            _logger.info('Set default EraSpy Base URL to https://spy.era.net.sa/api')

    def action_eraspy_fix_stuck_queued_applicants(self):
        self.ensure_one()
        applicant_model = self.env["hr.applicant"].sudo()

        queued_applicants = applicant_model.search([("eraspy_last_status", "ilike", "queued")])
        if not queued_applicants:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("EraSpy"),
                    "message": _("No applicants with 'Queued' EraSpy status were found."),
                    "type": "warning",
                    "sticky": False,
                },
            }

        queued_applicants.write({
            "eraspy_last_status": "failed",
            "eraspy_last_checked": fields.Datetime.now(),
        })
        fixed_count = len(queued_applicants)

        _logger.info("EraSpy queued applicant fix executed: fixed=%s", fixed_count)

        message = _(
            "Updated %(fixed)s applicant(s) from 'Queued' to 'Failed'."
        ) % {"fixed": fixed_count}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("EraSpy"),
                "message": message,
                "type": "success",
                "sticky": False,
            },
        }

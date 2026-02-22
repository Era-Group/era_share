# -*- coding: utf-8 -*-
import logging

from odoo import _, fields, models

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def action_eraspy_fix_stuck_queued_applicants(self):
        self.ensure_one()
        applicant_model = self.env["hr.applicant"].sudo()
        queue_model = self.env["eraspy.applicant.callback.queue"].sudo()

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

        pending_groups = queue_model.read_group(
            [("state", "=", "pending"), ("applicant_id", "in", queued_applicants.ids)],
            fields=["applicant_id"],
            groupby=["applicant_id"],
        )
        pending_ids = {group["applicant_id"][0] for group in pending_groups if group.get("applicant_id")}
        stuck_applicants = queued_applicants.filtered(lambda applicant: applicant.id not in pending_ids)
        fixed_count = len(stuck_applicants)

        if stuck_applicants:
            stuck_applicants.write(
                {
                    "eraspy_last_status": "failed",
                    "eraspy_last_checked": fields.Datetime.now(),
                }
            )

        _logger.info(
            "EraSpy queued applicant fix executed: queued=%s fixed=%s pending=%s",
            len(queued_applicants),
            fixed_count,
            len(pending_ids),
        )

        message = _(
            "Checked %(queued)s queued applicant(s). Updated %(fixed)s applicant(s) to failed "
            "because no pending callback record exists."
        ) % {"queued": len(queued_applicants), "fixed": fixed_count}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("EraSpy"),
                "message": message,
                "type": "success" if fixed_count else "warning",
                "sticky": False,
            },
        }

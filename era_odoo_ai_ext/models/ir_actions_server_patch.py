from odoo import models
from odoo.exceptions import UserError, ValidationError


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def _ai_tool_run(self, record, arguments):
        try:
            return super()._ai_tool_run(record, arguments)
        except (ValidationError, UserError) as error:
            message = (str(error) or "").strip()
            return message or self.env._("The tool request is missing required information.")

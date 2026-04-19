# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError


class YusrSetPinWizard(models.TransientModel):
    _name = 'yusr.set.pin.wizard'
    _description = 'Set Employee Yusr PIN'

    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
    )
    new_pin = fields.Char(string='New PIN', required=True)
    confirm_pin = fields.Char(string='Confirm PIN', required=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_model') == 'hr.employee':
            res['employee_id'] = self.env.context.get('active_id')
        return res

    def action_set_pin(self):
        self.ensure_one()
        if not self.employee_id:
            raise UserError(_("No employee selected."))
        if self.new_pin != self.confirm_pin:
            raise ValidationError(_("PIN confirmation does not match."))
        if not re.match(r'^\d{4,6}$', self.new_pin):
            raise ValidationError(_("PIN must be 4 to 6 digits."))

        self.employee_id.set_pin(self.new_pin)
        self.employee_id.message_post(
            body=_("Yusr PIN has been set/reset by %s.") % self.env.user.name
        )
        return {'type': 'ir.actions.act_window_close'}

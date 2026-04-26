from odoo import _, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    def action_open_reassign_wizard(self):
        self.ensure_one()
        return {
            'name': _("Reassign Work of %s", self.name),
            'type': 'ir.actions.act_window',
            'res_model': 'era.emp.reassign.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_from_user_id': self.id,
            },
        }

from odoo import fields, models




class HrEmployee(models.Model):
    _inherit = 'hr.employee'
    last_visa_number = fields.Char(string="Last Visanumber" )
    borderNumber = fields.Char(string="BorderNumber")
    expriry_pass_date = fields.Date(string="PassportExpiry")
    expriry_date_iqama = fields.Date(string="IqamaExpiry")

    def _get_is_admin(self):
        """
        Compute method to check if the user is an admin.
        """
        for rec in self:
            rec.is_admin = False
            if self.env.user.id == self.env.ref('base.user_admin').id :
                rec.is_admin = True

    is_admin = fields.Boolean(compute=_get_is_admin, string="Is Admin",
                              help='Check if the user is an admin.')

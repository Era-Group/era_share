# -*- coding: utf-8 -*-
import re
from werkzeug.security import generate_password_hash, check_password_hash

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, AccessDenied


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    # --- Yusr mobile app credentials ---
    employee_login_id = fields.Char(
        string='Employee Login ID',
        copy=False,
        index=True,
        help="Unique identifier used to log into the Yusr mobile app "
             "(e.g., EMP1023). Case-insensitive, alphanumeric."
    )
    pin_hash = fields.Char(
        string='PIN Hash',
        copy=False,
        groups='hr.group_hr_user',
        help="Hashed PIN code. Never stored in plain text."
    )
    yusr_access_enabled = fields.Boolean(
        string='Yusr Access Enabled',
        default=True,
        help="When unchecked, the employee cannot log into the Yusr app."
    )
    yusr_last_login = fields.Datetime(
        string='Last Yusr Login',
        copy=False,
        readonly=True,
    )
    yusr_failed_attempts = fields.Integer(
        string='Failed Login Attempts',
        default=0,
        copy=False,
    )
    yusr_locked_until = fields.Datetime(
        string='Locked Until',
        copy=False,
        help="If set in the future, login is blocked until this time."
    )

    _sql_constraints = [
        ('employee_login_id_unique',
         'unique(employee_login_id)',
         'Employee Login ID must be unique.'),
    ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains('employee_login_id')
    def _check_employee_login_id_format(self):
        pattern = re.compile(r'^[A-Za-z0-9_-]{3,32}$')
        for emp in self:
            if emp.employee_login_id and not pattern.match(emp.employee_login_id):
                raise ValidationError(_(
                    "Employee Login ID must be 3-32 characters, "
                    "alphanumeric, underscore or dash only."
                ))

    # ------------------------------------------------------------------
    # PIN management
    # ------------------------------------------------------------------
    def set_pin(self, pin):
        """Hash and store a PIN. Validates format (4-6 digits)."""
        self.ensure_one()
        if not pin or not re.match(r'^\d{4,6}$', str(pin)):
            raise ValidationError(_("PIN must be 4 to 6 digits."))
        self.sudo().write({
            'pin_hash': generate_password_hash(str(pin), method='pbkdf2:sha256', salt_length=16),
            'yusr_failed_attempts': 0,
            'yusr_locked_until': False,
        })
        return True

    def verify_pin(self, pin):
        """Check a PIN against the stored hash. Returns True/False."""
        self.ensure_one()
        if not self.pin_hash or not pin:
            return False
        try:
            return check_password_hash(self.pin_hash, str(pin))
        except Exception:
            return False

    def action_reset_pin(self):
        """Admin action: clear PIN so employee can set a new one via HR."""
        for emp in self:
            emp.sudo().write({
                'pin_hash': False,
                'yusr_failed_attempts': 0,
                'yusr_locked_until': False,
            })
        return True

    # ------------------------------------------------------------------
    # Authentication helpers (called by controllers)
    # ------------------------------------------------------------------
    @api.model
    def authenticate_yusr(self, login_id, pin):
        """
        Find employee by login_id and verify PIN.
        Implements lockout after 5 consecutive failed attempts (15 min).
        Returns the employee record on success. Raises AccessDenied on failure.
        """
        from datetime import datetime, timedelta

        MAX_ATTEMPTS = 5
        LOCKOUT_MINUTES = 15

        if not login_id or not pin:
            raise AccessDenied(_("Login ID and PIN are required."))

        emp = self.sudo().search([
            ('employee_login_id', '=ilike', login_id),
            ('active', '=', True),
        ], limit=1)

        if not emp:
            raise AccessDenied(_("Invalid credentials."))

        if not emp.yusr_access_enabled:
            raise AccessDenied(_("Your Yusr access is disabled. Contact HR."))

        # Check lockout
        if emp.yusr_locked_until and emp.yusr_locked_until > fields.Datetime.now():
            raise AccessDenied(_(
                "Account locked due to too many failed attempts. Try again later."
            ))

        if not emp.verify_pin(pin):
            # Increment failed attempts
            new_count = emp.yusr_failed_attempts + 1
            vals = {'yusr_failed_attempts': new_count}
            if new_count >= MAX_ATTEMPTS:
                vals['yusr_locked_until'] = datetime.now() + timedelta(minutes=LOCKOUT_MINUTES)
                vals['yusr_failed_attempts'] = 0
            emp.sudo().write(vals)
            raise AccessDenied(_("Invalid credentials."))

        # Success - reset counters and stamp login time
        emp.sudo().write({
            'yusr_failed_attempts': 0,
            'yusr_locked_until': False,
            'yusr_last_login': fields.Datetime.now(),
        })
        return emp

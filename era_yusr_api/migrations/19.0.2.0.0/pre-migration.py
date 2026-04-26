# -*- coding: utf-8 -*-
"""Drop the obsolete hr_employee.pin_hash column.

Mobile authentication has switched to the standard hr.employee.pin field
(same one used by the Attendance Kiosk). Legacy PIN hashes stored on
hr_employee.pin_hash cannot be reused as plain-text PINs, so they are
dropped outright. Employees who had a Yusr PIN but no Kiosk PIN will
need HR to set a new PIN via the "Set Yusr PIN" wizard or the standard
Odoo PIN field.
"""


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE hr_employee DROP COLUMN IF EXISTS pin_hash;
    """)

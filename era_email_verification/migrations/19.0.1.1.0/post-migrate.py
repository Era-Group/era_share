"""On update, grant existing system administrators the Email Verification
Manager role so the Settings section and menu become visible.

Needed because the group is created in a ``noupdate`` data file, so a plain
``-u`` cannot add members to it through XML on an already-installed database.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    manager = env.ref(
        "era_email_verification.group_email_verification_manager",
        raise_if_not_found=False)
    if not manager:
        return
    system = env.ref("base.group_system", raise_if_not_found=False)
    if not system:
        return
    admins = env["res.users"].search([("all_group_ids", "in", system.id)])
    if admins:
        manager.sudo().write({"user_ids": [(4, user.id) for user in admins]})

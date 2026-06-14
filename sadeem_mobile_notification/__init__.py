from odoo import api, SUPERUSER_ID
from odoo.exceptions import UserError

from . import controllers
from . import models


def pre_init_hook(env):
    """Check if conflicting module is installed"""
    conflicting_module = env['ir.module.module'].search([
        ('name', '=', 'sadeem_saas_slave'),
        ('state', '=', 'installed'),
    ], limit=1)

    if conflicting_module:
        raise UserError(
            "Cannot install this module because 'sadeem_saas_slave' is already installed. "
            "Please uninstall it first."
        )

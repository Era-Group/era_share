# -*- coding: utf-8 -*-
"""Regenerate the JWT signing secret on module upgrade.

Same rotation as 19.0.1.1.0 — bumping the manifest version is the
explicit policy gate for invalidating every existing mobile session.
"""
from odoo import api, SUPERUSER_ID

from odoo.addons.era_yusr_api.utils.jwt_helper import (
    CONFIG_PARAM_KEY,
    generate_secret,
)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['ir.config_parameter'].sudo().set_param(
        CONFIG_PARAM_KEY, generate_secret()
    )

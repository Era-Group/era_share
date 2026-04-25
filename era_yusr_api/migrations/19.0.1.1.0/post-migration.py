# -*- coding: utf-8 -*-
"""Regenerate the JWT signing secret on module upgrade.

This script runs automatically whenever Odoo applies the 19.0.1.1.0
version bump (or any later version, via sibling directories). Rotating
the secret invalidates every existing mobile session, which is the
intended behaviour for a redeploy: the policy gate is the manifest
version bump.

For future regenerations, bump `version` in __manifest__.py and add a
sibling migration directory (e.g. migrations/19.0.1.2.0/post-migration.py)
that calls `generate_secret()` the same way.
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

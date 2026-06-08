# -*- coding: utf-8 -*-
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    """Register/refresh the Monte Carlo statistics AI tool on upgrade.

    post_init_hook only runs on first install, so on an already-installed
    database the tool is (re)registered here during ``-u``. No-op when the
    EE ``ai`` module is absent.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["monte.carlo.variable"]._setup_ai_statistics_tool()

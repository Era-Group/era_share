# -*- coding: utf-8 -*-
def migrate(cr, version):
    """Create the dedicated 'Monte Carlo Narrator' AI agent on upgrade too
    (post_init_hook only runs on a fresh install). No-op without Odoo AI."""
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    if "monte.carlo.run" in env:
        env["monte.carlo.run"]._mc_ensure_narrator_agent()

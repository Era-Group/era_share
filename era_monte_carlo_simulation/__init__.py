# -*- coding: utf-8 -*-
from . import models


def post_init_hook(env):
    """Register the statistics AI tool and the dedicated narrator agent (both
    no-ops when Odoo AI is not installed)."""
    env["monte.carlo.variable"]._setup_ai_statistics_tool()
    env["monte.carlo.run"]._mc_ensure_narrator_agent()


def uninstall_hook(env):
    """Drop the module's configuration parameters. The narrator agent and its
    utm.source carry module xml ids, so the standard uninstall removes them."""
    env["ir.config_parameter"].sudo().search(
        [("key", "=like", "era_monte_carlo_simulation.%")]).unlink()

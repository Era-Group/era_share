# -*- coding: utf-8 -*-
from . import models


def post_init_hook(env):
    """Register the statistics AI tool and the dedicated narrator agent (both
    no-ops when Odoo AI is not installed)."""
    env["monte.carlo.variable"]._setup_ai_statistics_tool()
    env["monte.carlo.run"]._mc_ensure_narrator_agent()

# -*- coding: utf-8 -*-
from . import models
from . import controllers
from . import wizard
from . import utils


def _post_init_hook(env):
    """Generate a fresh JWT signing secret on module install."""
    from .utils.jwt_helper import CONFIG_PARAM_KEY, generate_secret
    env['ir.config_parameter'].sudo().set_param(
        CONFIG_PARAM_KEY, generate_secret()
    )

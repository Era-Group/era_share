# -*- coding: utf-8 -*-
from . import models

from .models.tour_request import DEFAULT_MATCH_THRESHOLD

# Written once, at install, so the Settings screen shows what the code is
# actually doing. A config_parameter field whose parameter was never written
# reads as 0.0, and an administrator looking at "Answer confidence: 0.00" is
# being told the assistant answers anything at any agreement — which is not
# what the model does, since _match_threshold() rejects a value outside
# (0, 1] and falls back to this same number.
INSTALL_DEFAULTS = {
    "era_web_tour_assistant.match_threshold": str(DEFAULT_MATCH_THRESHOLD),
}


def _set_install_defaults(env):
    """Fill in the parameters the Settings screen shows, without overwriting.

    Only absent keys are written, so a database where somebody has tuned the
    confidence keeps their number through every later upgrade of this module.

    ``answer_source`` is deliberately not here. It is not on the Settings
    screen, and pinning it into the database on install would freeze whatever
    this module's default happened to be that day — an install from before the
    default was widened would keep answering nothing forever, with a parameter
    nobody set explaining why.
    """
    params = env["ir.config_parameter"].sudo()
    for key, value in INSTALL_DEFAULTS.items():
        if not params.get_param(key):
            params.set_param(key, value)

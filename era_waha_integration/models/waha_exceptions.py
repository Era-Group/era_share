# Part of Era Group custom addons.
from odoo.exceptions import UserError


class WahaSendLimit(UserError):
    """A WAHA send blocked by an account-protection rule. Surfaces as a dialog to
    the user; the request transaction rolls back so nothing appears as a failed
    message in the conversation."""


class WahaNewNumberLimit(WahaSendLimit):
    """The per-user daily new-number cap is reached. Like WahaSendLimit it is
    surfaced (via the guard wrapper) as a plain UserError so the composer stays
    open with the text and the user can send via the official WhatsApp instead."""

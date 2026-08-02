"""Convert the single "Auto-blacklist policy" selection into one independent
checkbox per outcome.

The old ``era_email_verification.blacklist_policy`` parameter held one of five
combined values; each maps onto the new per-outcome booleans without changing
what any existing database blacklists. ``blacklist_catch_all`` keeps its own
key and value — it simply moved into the same checkbox group.
"""
from odoo import SUPERUSER_ID, api

_OLD_KEY = "era_email_verification.blacklist_policy"

# old policy -> outcomes switched on. Everything unlisted stays off, and an
# unknown/absent value falls back to the shipped default (undeliverable+risky).
_POLICY_MAP = {
    "off": (),
    "undeliverable": ("undeliverable",),
    "undeliverable_disposable": ("undeliverable", "disposable"),
    "undeliverable_risky": ("undeliverable", "risky"),
    "undeliverable_risky_unknown": ("undeliverable", "risky", "unknown"),
}
_OUTCOMES = ("undeliverable", "risky", "disposable", "unknown")

# Other boolean settings, with the default each one used to fall back to when
# its parameter was absent. They are rewritten explicitly so that unticking a
# box now sticks (see ResConfigSettings._ev_persist_boolean_params); the value
# written is the one the database is behaving with today, so nothing changes.
_BOOLEAN_DEFAULTS = {
    "era_email_verification.verify_tls": True,
    "era_email_verification.default_check_smtp": True,
    "era_email_verification.default_check_catch_all": True,
    "era_email_verification.auto_recheck": False,
    "era_email_verification.push_enabled": True,
}

_TRUTHY = ("1", "true", "yes", "on")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()

    policy = icp.get_param(_OLD_KEY)
    enabled = _POLICY_MAP.get(policy, ("undeliverable", "risky"))
    for outcome in _OUTCOMES:
        icp.set_param(
            "era_email_verification.blacklist_" + outcome,
            str(outcome in enabled))

    # Normalise the remaining booleans (catch-all included) to an explicit
    # "True"/"False", keeping whatever each database behaves with today.
    for key, default in dict(
            _BOOLEAN_DEFAULTS,
            **{"era_email_verification.blacklist_catch_all": False}).items():
        current = str(icp.get_param(key, default)).strip().lower()
        icp.set_param(key, str(current in _TRUTHY))

    # Drop the superseded parameter and its XML-ID so a later -u cannot
    # resurrect it.
    old = icp.search([("key", "=", _OLD_KEY)])
    if old:
        env["ir.model.data"].search([
            ("model", "=", "ir.config_parameter"),
            ("res_id", "in", old.ids),
        ]).unlink()
        old.unlink()

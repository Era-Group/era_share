"""Shared vocabulary for the Email Verification module.

Kept free of Odoo model classes so it can be imported by models, wizards and
tests without side effects. The status strings mirror exactly what the Email
Verifier API returns (``deliverable`` / ``undeliverable`` / ``risky`` /
``unknown``) plus the local ``not_checked`` placeholder.
"""

# -- verification statuses ----------------------------------------------------
STATUS_NOT_CHECKED = "not_checked"
STATUS_DELIVERABLE = "deliverable"
STATUS_UNDELIVERABLE = "undeliverable"
STATUS_RISKY = "risky"
STATUS_UNKNOWN = "unknown"

# The four terminal statuses the API can return.
API_STATUSES = (STATUS_DELIVERABLE, STATUS_UNDELIVERABLE, STATUS_RISKY, STATUS_UNKNOWN)

# Selection used on res.partner / mailing.contact (ordered best -> worst).
STATUS_SELECTION = [
    (STATUS_NOT_CHECKED, "Not checked"),
    (STATUS_DELIVERABLE, "Deliverable"),
    (STATUS_RISKY, "Risky"),
    (STATUS_UNKNOWN, "Unknown"),
    (STATUS_UNDELIVERABLE, "Undeliverable"),
]

# Decoration/badge colour per status, for the list/kanban widgets.
STATUS_DECORATION = {
    STATUS_DELIVERABLE: "success",
    STATUS_RISKY: "warning",
    STATUS_UNKNOWN: "muted",
    STATUS_UNDELIVERABLE: "danger",
    STATUS_NOT_CHECKED: "info",
}

# -- item processing states ---------------------------------------------------
ITEM_PENDING = "pending"      # queued locally, not yet imported from the API
ITEM_DONE = "done"            # a result was imported and written to the record
ITEM_STALE = "stale"          # the record's email changed before import; skipped
ITEM_ERROR = "error"          # a per-item error was recorded

ITEM_STATE_SELECTION = [
    (ITEM_PENDING, "Pending"),
    (ITEM_DONE, "Imported"),
    (ITEM_STALE, "Stale (email changed)"),
    (ITEM_ERROR, "Error"),
]

# -- verifier API request contract --------------------------------------------
# The verifier's /v1/jobs request model rejects the ENTIRE job with HTTP 422
# when any single entry falls outside these bounds, so anything out of range
# has to be filtered out before submitting rather than sent and sorted later.
MIN_EMAIL_LENGTH = 3
MAX_EMAIL_LENGTH = 320

# -- blacklist policy ---------------------------------------------------------
# The policy is a set of independent outcomes, one checkbox each in Settings ▸
# Automatic blacklist. An outcome whose box is off is never auto-blacklisted,
# so "nothing checked" is the old "Off" policy.
BLACKLIST_OUTCOMES = ("undeliverable", "risky", "disposable", "unknown", "catch_all")

# Shipped defaults: hard bounces and plain risky addresses. Catch-all stays off
# (it means "unverifiable", not "invalid"), and so do disposable-only and
# unknown, which are opt-in.
DEFAULT_BLACKLIST_OUTCOMES = {
    "undeliverable": True,
    "risky": True,
    "disposable": False,
    "unknown": False,
    "catch_all": False,
}


def should_blacklist(policy, status, flags):
    """Return True if an address with this (status, flags) must be blacklisted.

    ``policy`` maps each outcome in ``BLACKLIST_OUTCOMES`` to a bool (one
    settings checkbox each); ``flags`` is the verifier ``flags`` dict (may be
    falsy). The boxes are independent — an address is blacklisted as soon as
    one box that describes it is on.

    Two outcomes overlap with ``risky`` and are resolved before it:

    * *disposable* — the verifier reports throwaway domains as ``risky`` with
      the ``disposable`` flag, so its own box must catch them even when Risky
      is off (Risky being on still covers them);
    * *catch-all* — answered by its own box **instead of** Risky, because a
      catch-all domain accepts *every* address: "risky" there means the mailbox
      could not be *confirmed*, NOT that it is invalid, and a perfectly valid
      mailbox can live on such a domain.
    """
    policy = policy or {}
    flags = flags or {}
    if status == STATUS_UNDELIVERABLE:
        return bool(policy.get("undeliverable"))
    if flags.get("disposable") and policy.get("disposable"):
        return True
    if status == STATUS_RISKY:
        if flags.get("catch_all"):
            return bool(policy.get("catch_all"))
        return bool(policy.get("risky"))
    if status == STATUS_UNKNOWN:
        return bool(policy.get("unknown"))
    return False


def wants_true(operator, value):
    """Normalize a boolean search leaf to "is the caller asking for True?".

    Odoo does not hand a ``search=`` method the operator verbatim: a plain
    ``('field', '=', True)`` leaf reaches it normalized to ``('in', [True])``,
    so testing ``operator == '='`` alone silently inverts the search.
    """
    if operator in ("in", "not in"):
        values = value if isinstance(value, (list, tuple, set)) else [value]
        wanted = any(bool(item) for item in values)
        return wanted if operator == "in" else not wanted
    if operator in ("=", "!=", "=="):
        return bool(value) if operator != "!=" else not bool(value)
    raise NotImplementedError(
        "Unsupported operator %r for a boolean search field" % (operator,))


def is_eligible(status, score, flags, min_score):
    """Stored mailing-eligibility policy.

    Eligible = a clean deliverable that is not disposable and scores at least
    ``min_score``. Risky (incl. catch-all), unknown, undeliverable and
    disposable are never eligible. This never *proves* consent or engagement —
    it only reflects the technical verification result.
    """
    flags = flags or {}
    return bool(
        status == STATUS_DELIVERABLE
        and not flags.get("disposable")
        and (score or 0) >= (min_score or 0)
    )

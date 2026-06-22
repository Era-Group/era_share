# -*- coding: utf-8 -*-
import logging
import os

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CrmAiLeadGenProvider(models.Model):
    """A single lead-generation SOURCE in the prospecting waterfall.

    Module 16 (Lead Gen) discovers NET-NEW B2B companies and their
    decision-makers from external providers. Each provider is described by one
    row of this model: what kind of source it is, what category of data it
    brings, where it sits in the waterfall (``priority``), what it costs, which
    fields it fills, and — crucially — the NAME of the environment variable that
    must hold its API token (``env_key_param``). The secret itself is NEVER
    stored here or anywhere in the DB (Rule 03); we keep only the variable name
    and compute, on demand, whether that variable currently resolves to a value
    (``token_present``).

    Source activation is gated by THREE independent checks (enforced by the
    engine's ``_eligible_providers`` gate — the single source of truth — see the
    16.00 overview):

    1. ``active`` (manual toggle, default OFF) — an operational/legal decision
       that overrides everything. A provider that is not active is never used,
       even if its token exists.
    2. ``token_present`` (technical) — the env var named in ``env_key_param``
       must resolve to a non-empty value. An active provider with a MISSING
       token is skipped automatically, but NEVER silently: a ``blocked`` warning
       is written to the Base audit log (same philosophy as the Base's
       ``block_unpriced_model`` fail-safe).
    3. PDPL legal permission (separate, configured in 16.7) — independent of the
       above. A token existing never implies a source is lawful to use; the
       ``pdpl_sensitivity`` flag marks decision-maker sources as ``heavy`` so
       they can be gated behind the conservative people-fetch toggle.

    The model name stays ``crm.ai.*`` (no ``era_`` prefix) per the naming
    convention; only the MODULE carries the ``era_`` prefix.
    """

    _name = "crm.ai.lead_gen.provider"
    _description = "CRM AI Lead-Gen Source / Provider"
    # Waterfall order: lower ``priority`` runs first; ``name`` breaks ties so the
    # ordering is stable. Satisfies "providers are orderable by priority".
    _order = "priority, name"

    name = fields.Char(required=True, translate=True)

    provider_type = fields.Selection(
        selection=[
            ("web_search", "Web Search API"),
            ("local_registry", "Local Business Registry"),
            ("web_scrape", "Public-Page Scrape"),
            ("contact_data", "Contact-Data Provider"),
            ("social", "Social Network"),
        ],
        required=True,
        help="The TECHNICAL kind of source. Drives which handler (added in 16.4) "
             "knows how to query it.",
    )

    category = fields.Selection(
        selection=[
            ("company", "Company Data"),
            ("decision_maker", "Decision-Maker (Individual)"),
            ("verification", "Channel / Email Verification"),
        ],
        required=True,
        help="What this source brings into the system. 'company' is lighter "
             "under PDPL; 'decision_maker' (individuals) is the heaviest part and "
             "is gated behind a separate, off-by-default toggle.",
    )

    priority = fields.Integer(
        default=10,
        help="Waterfall position — LOWER runs first. Sources are tried in this "
             "order until the needed data is found.",
    )

    cost = fields.Float(
        digits=(12, 6),
        help="Estimated cost per successful fetch from this source, in USD. Feeds "
             "the Rule 14 consumption tracking added in later tasks (cost must "
             "count BOTH the source-API call and any LLM-extraction call).",
    )

    fields_filled = fields.Char(
        help="Free-text note of which partner fields this source typically "
             "fills (e.g. 'name, website, vat' or 'email, phone, job_title'). "
             "Documentation/targeting aid, not enforced here.",
    )

    active = fields.Boolean(
        default=False,
        help="Manual master toggle for THIS source (default OFF). An operational "
             "and legal decision that overrides everything: an inactive source is "
             "never used, even if its token is present. PDPL activation requires "
             "explicit review.",
    )

    env_key_param = fields.Char(
        string="Token Env Var Name",
        help="The NAME of the environment variable that holds this source's API "
             "token — never the secret itself (Rule 03). Leave empty for sources "
             "that need no token (e.g. some public scrapes).",
    )

    token_present = fields.Boolean(
        string="Token Present",
        compute="_compute_token_present",
        # Non-stored on purpose: it reflects the LIVE process environment, which
        # can change without any DB write, so it must be re-read on each access
        # rather than cached in a column.
        store=False,
        help="True only if the environment variable named in 'Token Env Var "
             "Name' currently resolves to a non-empty value. Read straight from "
             "the process environment (os.getenv) — the secret is never stored "
             "or pulled through the DB, so no sudo elevation is involved.",
    )

    pdpl_sensitivity = fields.Selection(
        selection=[
            ("light", "Light (B2B / company)"),
            ("heavy", "Heavy (personal data)"),
        ],
        default="light",
        required=True,
        help="PDPL weight of the data this source brings. Decision-maker / "
             "personal-data sources MUST be 'heavy'; they are gated behind the "
             "conservative people-fetch toggle (16.7).",
    )

    @api.depends("env_key_param")
    def _compute_token_present(self):
        """Resolve token presence from the LIVE process environment only.

        We read ``os.getenv(env_key_param)`` and store only the resulting boolean
        — never the value. This is the strictest Rule 03 reading: the secret is
        not written to the DB and is not even pulled through the ORM, so this
        compute needs NO sudo elevation (unlike the Base cap read, which uses a
        narrow approved sudo for a policy NUMBER, not a secret).
        """
        for provider in self:
            key = (provider.env_key_param or "").strip()
            value = os.getenv(key) if key else None
            provider.token_present = bool(value and value.strip())

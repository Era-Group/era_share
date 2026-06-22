# -*- coding: utf-8 -*-
"""Manager-facing Lead-Generation settings (No-Hardcoded-Policy rule).

Every targeting value and master toggle is an ``ir.config_parameter`` under the
``era_crm_ai_agents_lead_gen.*`` namespace, bound via ``config_parameter=`` so
Odoo reads/writes the param automatically. Defaults are deliberately
CONSERVATIVE (the whole module OFF, decision-maker fetching OFF, no targeting
assumed) so an untouched install does nothing until a manager reviews PDPL and
opts in — see the 16.00 overview.

The fields render on the global Settings page inside the Base's "CRM AI Agents"
app block, which is gated to ``group_crm_ai_manager`` — so these toggles are
manager-only (satisfying the 16.2 "non-managers cannot edit PDPL toggles"
criterion). See views/res_config_settings_views.xml.

The ``set_values`` override mirrors the Compliance module fix: it writes booleans
as explicit ``'True'``/``'False'`` strings so a toggle turned OFF persists
instead of being dropped (the "first-OFF-lost" bug — ``set_param`` unlinks a
param handed a Python ``False``). The companion data/ir_config_parameter_data.xml
seeds every param at install so it always EXISTS from the first save.
"""
from odoo import fields, models

# Namespace for every lead-gen config parameter. Kept as one constant so the
# set_values filter and the seed data stay in lock-step.
PARAM_PREFIX = "era_crm_ai_agents_lead_gen."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # -- Master toggles (both default OFF — conservative) -------------------
    lead_gen_enabled = fields.Boolean(
        string="Enable Lead Generation",
        default=False,
        help="Master switch for the whole prospecting engine. OFF (default) = "
             "no source is ever queried, regardless of per-source toggles.",
        config_parameter=PARAM_PREFIX + "enabled",
    )
    lead_gen_fetch_decision_makers = fields.Boolean(
        string="Fetch Decision-Makers (individuals)",
        default=False,
        help="Allow fetching named individuals (decision-makers). OFF (default) "
             "= only company-level data is gathered. This is the HEAVIEST part "
             "under PDPL; keep it off until a legal review approves it.",
        config_parameter=PARAM_PREFIX + "fetch_decision_makers",
    )

    # -- Targeting (free-text, comma-separated; empty = unconstrained) ------
    lead_gen_target_sectors = fields.Char(
        string="Target Sectors",
        help="Comma-separated sectors/industries to prospect (e.g. "
             "'Construction, Logistics, Healthcare'). Empty = no sector filter.",
        config_parameter=PARAM_PREFIX + "target_sectors",
    )
    lead_gen_target_regions = fields.Char(
        string="Target Regions / Cities",
        help="Comma-separated regions or cities (e.g. 'Jeddah, Riyadh, Dammam'). "
             "Empty = no region filter.",
        config_parameter=PARAM_PREFIX + "target_regions",
    )
    lead_gen_target_company_size = fields.Char(
        string="Target Company Size",
        help="Comma-separated size bands to target (e.g. '11-50, 51-200'). Free "
             "text so the bands stay manager-editable. Empty = any size.",
        config_parameter=PARAM_PREFIX + "target_company_size",
    )
    lead_gen_target_job_titles = fields.Char(
        string="Target Job Titles (decision-makers)",
        help="Comma-separated decision-maker titles to look for (e.g. 'CEO, "
             "Procurement Manager, CFO'). Used only when decision-maker fetching "
             "is enabled. Empty = no title filter.",
        config_parameter=PARAM_PREFIX + "target_job_titles",
    )

    # -- Creation: tag + de-duplication (16.5) -----------------------------
    lead_gen_tag_name = fields.Char(
        string="Lead-Gen Tag Name",
        help="Partner tag stamped on every record the engine CREATES, so "
             "externally-sourced contacts can be isolated or purged. Resolved by "
             "name; defaults to 'by_lead_generator_agent'.",
        config_parameter=PARAM_PREFIX + "tag_name",
    )
    lead_gen_dedup_mode = fields.Selection(
        selection=[
            ("skip", "Skip duplicates (leave existing untouched)"),
            ("update", "Update existing (fill blank fields only)"),
        ],
        string="On duplicate match",
        default="skip",
        help="What to do when an incoming record matches an existing partner. "
             "'skip' (default) never touches existing data; 'update' fills only "
             "the BLANK fields on the match. Neither ever creates a duplicate.",
        config_parameter=PARAM_PREFIX + "dedup_mode",
    )

    # -- PDPL / Compliance posture (16.7) ----------------------------------
    # Conservative by construction. These DESIGN FOR compliance; they do not
    # assert it (there is no approved Era Group PDPL reference on file yet).
    # Enforcement of "no outreach without consent" lives DOWNSTREAM in the
    # Compliance layer (#1) — these settings record the stance Lead Gen takes.
    lead_gen_require_compliance_check = fields.Boolean(
        string="Route through Compliance before outreach",
        default=True,
        help="When ON (default), externally-sourced records must pass the "
             "Compliance guard (#1) before ANY outreach. Lead Gen only creates "
             "records; this records the posture the Compliance layer enforces.",
        config_parameter=PARAM_PREFIX + "require_compliance_check",
    )
    lead_gen_decision_maker_notice = fields.Selection(
        selection=[("required", "Required"), ("not_required", "Not required")],
        string="Decision-maker notice",
        default="required",
        help="Posture on giving individuals (decision-makers) a processing "
             "notice. Conservative default = required. Documented stance; "
             "Compliance enforces it before outreach.",
        config_parameter=PARAM_PREFIX + "decision_maker_notice",
    )
    lead_gen_decision_maker_consent = fields.Selection(
        selection=[("required", "Required"), ("not_required", "Not required")],
        string="Decision-maker consent",
        default="required",
        help="Posture on obtaining consent before processing an individual's "
             "personal data. Conservative default = required. Documented stance; "
             "Compliance enforces it before outreach.",
        config_parameter=PARAM_PREFIX + "decision_maker_consent",
    )
    lead_gen_data_residency_prefer_local = fields.Boolean(
        string="Prefer local (in-region) sources",
        default=True,
        help="When ON (default), the waterfall favours in-region sources (the "
             "Saudi registries sit at the top of the priority order) for data "
             "residency.",
        config_parameter=PARAM_PREFIX + "data_residency_prefer_local",
    )
    lead_gen_data_residency_flag_egress = fields.Boolean(
        string="Flag when data leaves the region",
        default=True,
        help="When ON (default), the engine writes an audit flag whenever a "
             "non-Saudi-local source is used, so cross-border data movement is "
             "never silent.",
        config_parameter=PARAM_PREFIX + "data_residency_flag_egress",
    )

    # -- Daily fetch cap (16.6) --------------------------------------------
    lead_gen_daily_cap = fields.Integer(
        string="Daily Fetch Cap (records/day)",
        default=50,
        help="Maximum records the engine may CREATE per day. Fail-safe like the "
             "cost cap: 0 or negative = unlimited (explicit opt-out); a missing/"
             "unparseable value blocks fetching. Checked BEFORE each source call "
             "and again at each record creation.",
        config_parameter=PARAM_PREFIX + "daily_cap",
    )

    # -- Missing-token policy ----------------------------------------------
    lead_gen_block_source_without_token = fields.Selection(
        selection=[
            ("warn", "Warn & skip (audit the skip)"),
            ("silent", "Skip silently (no audit)"),
        ],
        string="Active source with missing token",
        default="warn",
        help="What to do when a source is active but its token env var is not "
             "set. 'warn' (default) skips the source AND writes an audit warning "
             "— never a silent skip (same philosophy as the Base's "
             "block_unpriced_model). 'silent' skips without auditing.",
        config_parameter=PARAM_PREFIX + "block_source_without_token",
    )

    def set_values(self):
        """Persist our boolean toggles as explicit ``'True'``/``'False'`` strings.

        ``ir.config_parameter.set_param`` UNLINKS a param when handed a Python
        ``False``, so the stock ``res.config.settings`` flow would silently drop a
        toggle turned OFF and any reader would fall back to a default. Writing the
        literal strings keeps the OFF choice stored and unambiguous. Char and
        Selection fields are already written correctly by ``super()`` (they are
        non-empty or harmless empty strings), so only booleans need fixing here.
        """
        super().set_values()
        icp = self.env["ir.config_parameter"]
        for name, field in self._fields.items():
            param = getattr(field, "config_parameter", None)
            if not param or not param.startswith(PARAM_PREFIX):
                continue
            if field.type == "boolean":
                icp.set_param(param, "True" if self[name] else "False")
            elif field.type == "integer":
                # Store explicitly so a 0 (e.g. daily_cap opt-out) persists as
                # '0' rather than risking a falsy-value drop.
                icp.set_param(param, str(self[name]))

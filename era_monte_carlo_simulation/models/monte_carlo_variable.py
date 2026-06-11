# -*- coding: utf-8 -*-
import json
import math
import re
from collections import Counter

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

# numpy is declared as an external dependency in the manifest, so importing it
# at module level is safe: Odoo refuses to load the module when it is missing.
import numpy as np

_CODE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class MonteCarloVariable(models.Model):
    _name = "monte.carlo.variable"
    _description = "Monte Carlo Input Variable"
    _order = "model_id, sequence, id"

    # The engine builds {code: samples}: a duplicate code would silently drop
    # one variable's draws. The Python constraint gives the friendly message;
    # this guarantees uniqueness under concurrency.
    _model_code_uniq = models.Constraint(
        "unique(model_id, code)",
        "The variable code must be unique within a simulation model.")

    name = fields.Char(
        string="Name", required=True,
        help="A readable label for this input (e.g. Number of Leads).")
    code = fields.Char(
        string="Code", required=True,
        help="Technical name used inside formulas (e.g. leads). "
             "Must be a valid identifier and unique within the model. "
             "The ready-made formulas expect specific codes "
             "(e.g. leads, conversion_rate, average_deal_value).")
    model_id = fields.Many2one(
        "monte.carlo.model", string="Simulation Model",
        required=True, ondelete="cascade", index=True,
        help="The simulation model this variable belongs to.")
    sequence = fields.Integer(
        string="Sequence", default=10,
        help="Controls the display order of variables. Drag the handle to "
             "reorder them.")
    description = fields.Text(
        string="Description",
        help="Optional notes about this input and where its values come from.")

    distribution = fields.Selection(
        selection=[
            ("fixed", "Fixed"),
            ("uniform", "Uniform"),
            ("normal", "Normal"),
            ("lognormal", "Log-normal"),
            ("triangular", "Triangular"),
            ("discrete", "Discrete"),
        ],
        string="Distribution", required=True, default="uniform",
        help="The probability distribution used to draw a random value for this "
             "input on each iteration. Fixed = no uncertainty; Uniform = equally "
             "likely between min and max; Normal = bell curve around a mean; "
             "Log-normal = strictly positive, right-skewed bell (use for prices, "
             "deal values, durations — it never goes below zero); "
             "Triangular = min/most-likely/max; Discrete = pick from a list.")

    fixed_value = fields.Float(
        string="Fixed Value",
        help="Fixed distribution: every iteration uses exactly this value "
             "(no uncertainty).")
    min_value = fields.Float(
        string="Minimum",
        help="Smallest possible value. Used by the Uniform and Triangular "
             "distributions; must be less than Maximum.")
    max_value = fields.Float(
        string="Maximum",
        help="Largest possible value. Used by the Uniform and Triangular "
             "distributions; must be greater than Minimum.")
    mean_value = fields.Float(
        string="Mean",
        help="Average (centre) of the Normal distribution. For Log-normal it is "
             "the average of the actual (positive) values you observe.")
    std_dev = fields.Float(
        string="Std. Deviation",
        help="Standard deviation: how widely values spread around the mean. "
             "Must be positive. Used by the Normal and Log-normal distributions.")
    mode_value = fields.Float(
        string="Mode",
        help="Most likely value (the peak) of the Triangular distribution; "
             "must lie between Minimum and Maximum.")
    discrete_values = fields.Char(
        string="Discrete Values",
        help="Discrete distribution: comma separated numbers, each drawn with "
             "equal probability, e.g. 10000, 25000, 50000.")

    distribution_summary = fields.Char(
        string="Parameters", compute="_compute_distribution_summary",
        help="Compact preview of this variable's distribution and its "
             "parameters.")

    # Live data source (optional) ---------------------------------------
    source_model_id = fields.Many2one(
        "ir.model", string="Source Model", ondelete="set null",
        help="Optional. Estimate this variable's parameters from your own "
             "Odoo data: pick a model (e.g. CRM Lead, Sales Order Line, "
             "Product) then click 'Refresh from data'.")
    source_mode = fields.Selection(
        selection=[("field", "Sample a field"),
                   ("count", "Count records per period"),
                   ("ratio", "Rate per period (matching ÷ total)")],
        string="Source Mode", default="field", required=True,
        help="'Sample a field' fits the distribution from a numeric field's "
             "values (e.g. average deal value). 'Count records per period' fits "
             "it from how many records occur each day/week/month (e.g. number of "
             "leads). 'Rate per period' fits it from a ratio each period — "
             "records matching the Numerator Filter divided by all records "
             "(e.g. a CRM conversion rate: won ÷ total).")
    source_success_domain = fields.Char(
        string="Numerator Filter", default="[]",
        help="Used by 'Rate per period'. Odoo domain selecting the 'success' "
             "subset (the numerator); the rate per period = records matching "
             "this ÷ all records. For a conversion rate use "
             "[('stage_id.is_won', '=', True)].")
    source_field_id = fields.Many2one(
        "ir.model.fields", string="Source Field", ondelete="set null",
        domain="[('model_id', '=', source_model_id),"
               " ('ttype', 'in', ('integer', 'float', 'monetary'))]",
        help="The numeric field to sample, e.g. Expected Revenue or Unit "
             "Price. Used when Source Mode is 'Sample a field'.")
    source_date_field_id = fields.Many2one(
        "ir.model.fields", string="Date Field", ondelete="set null",
        domain="[('model_id', '=', source_model_id),"
               " ('ttype', 'in', ('date', 'datetime'))]",
        help="The date field used to group records into periods when counting "
             "(e.g. Created On). Used when Source Mode is 'Count records per "
             "period'.")
    source_period = fields.Selection(
        selection=[("day", "Daily"), ("week", "Weekly"), ("month", "Monthly"),
                   ("quarter", "Quarterly"), ("year", "Yearly")],
        string="Period", default="month",
        help="Time bucket used when counting records (Monthly = number of "
             "records per month).")
    source_lookback = fields.Selection(
        selection=[("all", "All time"), ("1y", "Last 1 year"),
                   ("3y", "Last 3 years"), ("5y", "Last 5 years")],
        string="History Window", default="all",
        help="Limit the source records to a recent window using the Date Field "
             "('All time' uses everything). Handy to ignore old history.")
    source_domain = fields.Char(
        string="Source Filter", default="[]",
        help="Optional Odoo domain to filter the source records, e.g. "
             "[('create_date', '>=', '2025-01-01')].")
    source_sample_count = fields.Integer(
        string="Records Used", readonly=True, copy=False,
        help="Number of source records used by the last 'Refresh from data'.")

    @api.depends("distribution", "fixed_value", "min_value", "max_value",
                 "mean_value", "std_dev", "mode_value", "discrete_values")
    def _compute_distribution_summary(self):
        for record in self:
            dist = record.distribution
            if dist == "fixed":
                record.distribution_summary = _("fixed = %s") % record.fixed_value
            elif dist == "uniform":
                record.distribution_summary = _("uniform [%s, %s]") % (
                    record.min_value, record.max_value)
            elif dist == "normal":
                record.distribution_summary = _("normal (mean %s, sd %s)") % (
                    record.mean_value, record.std_dev)
            elif dist == "lognormal":
                record.distribution_summary = _(
                    "log-normal (mean %s, sd %s)") % (
                    record.mean_value, record.std_dev)
            elif dist == "triangular":
                record.distribution_summary = _("triangular (%s, %s, %s)") % (
                    record.min_value, record.mode_value, record.max_value)
            elif dist == "discrete":
                record.distribution_summary = _("discrete {%s}") % (
                    record.discrete_values or "")
            else:
                record.distribution_summary = ""

    # ------------------------------------------------------------------
    # Live data source
    # ------------------------------------------------------------------
    @api.onchange("source_model_id")
    def _onchange_source_model_id(self):
        """Drop stale fields when the source model changes."""
        self.source_field_id = False
        self.source_date_field_id = False

    def action_refresh_from_source(self):
        """Fit this variable's parameters from live Odoo data."""
        changes = []
        for variable in self:
            before = variable.distribution
            variable._refresh_from_source()
            if variable.distribution != before:
                changes.append((variable, before, variable.distribution))
        if changes:
            labels = dict(self._fields["distribution"]
                          ._description_selection(self.env))
            reasons = {
                "lognormal": _("a Normal fit on this positive data would have "
                               "produced impossible negative values"),
                "fixed": _("the source data has no variation"),
            }
            parts = []
            for variable, before, after in changes:
                parts.append(_(
                    "%(name)s: %(old)s → %(new)s (%(why)s)",
                    name=variable.name, old=labels.get(before, before),
                    new=labels.get(after, after),
                    why=reasons.get(after, _("better fit for the data"))))
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "warning",
                    "sticky": False,
                    "title": _("Distribution adjusted to fit the data"),
                    "message": "; ".join(parts),
                },
            }
        return True

    def action_ask_ai(self):
        """Open Odoo's standard AI chat seeded with a question to estimate this
        variable from the database (it uses Odoo AI's read_group/search tools).
        """
        self.ensure_one()
        if "ai.agent" not in self.env:
            raise UserError(_(
                "Odoo AI is not installed on this database."))
        # Pick a deliberate agent that carries the information-retrieval topic
        # (where the statistics tool is registered), not just the lowest id —
        # an arbitrary agent may not be able to call the data tools at all.
        Agent = self.env["ai.agent"]
        agent = self.env.ref(
            "ai.ai_agent_natural_language_search", raise_if_not_found=False)
        if not agent or not agent.exists():
            topic = self.env.ref("ai.ai_topic_information_retrieval_query",
                                 raise_if_not_found=False)
            if topic:
                agent = Agent.search(
                    [("topic_ids", "in", topic.ids)], limit=1)
        if not agent:
            agent = Agent.search([], limit=1)
        if not agent:
            raise UserError(_(
                "No Odoo AI agent is configured yet. Set one up in the AI app "
                "first."))
        # Make sure the statistics tool is wired (idempotent) — covers install
        # orders where the post_init/migration hook could not register it.
        self.env["monte.carlo.variable"]._setup_ai_statistics_tool()
        channel = agent._get_or_create_ai_chat()
        return {
            "type": "ir.actions.client",
            "tag": "agent_chat_action",
            "params": {
                "channelId": channel.id,
                "user_prompt": self._ai_estimate_prompt(),
            },
        }

    def _ai_estimate_prompt(self):
        """Build the seeded question sent to Odoo AI for this variable."""
        self.ensure_one()
        want = {
            "triangular": _("the minimum, the most likely value, and the maximum"),
            "normal": _("the mean and the standard deviation"),
            "uniform": _("the minimum and the maximum"),
            "fixed": _("a single representative value"),
            "discrete": _("a short list of representative values"),
        }.get(self.distribution, _("the typical values"))
        model_label = (self.source_model_id.name if self.source_model_id
                       else _("the most relevant records"))
        dist_label = dict(self._fields["distribution"]._description_selection(
            self.env)).get(self.distribution, self.distribution)
        return _(
            "I'm filling a Monte Carlo input variable named '%(name)s' "
            "(%(dist)s distribution). Using your data tools "
            "(monte_carlo_statistics / read_group / search) on %(model)s over "
            "the last 1 to 3 years, estimate %(want)s and reply with the exact "
            "numbers I should enter, plus a one-line note.",
            name=self.name or self.code, dist=dist_label,
            model=model_label, want=want)

    # ------------------------------------------------------------------
    # AI tool: precise statistics (mean/std/percentiles) for the agent
    # ------------------------------------------------------------------
    @staticmethod
    def _ai_parse_domain(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            pass
        try:
            return list(safe_eval(value))
        except Exception:  # noqa: BLE001
            # A non-empty string that parses as neither JSON nor a domain is an
            # error — do NOT fall back to [] (match-all), which would silently
            # return whole-table statistics to the AI.
            raise ValueError("Invalid domain (not valid JSON/Odoo domain): %s"
                             % (value,))

    # Cap on records fetched by the AI statistics tool in field mode: plenty
    # for a stable estimate, bounded so a broad domain cannot pull a whole
    # production table through one tool call.
    _AI_STATS_RECORD_CAP = 100000

    # -- shared statistics helpers (used by the AI tool AND the source
    # refresh, so the two paths cannot drift apart). All of them fetch ONLY
    # the needed column via search_fetch: a plain search().mapped() would
    # prefetch every stored field of every matching record.
    @api.model
    def _stats_field_values(self, records_model, domain, field_name, cap=None):
        """Finite numeric values of ``field_name`` for the matching records."""
        records = records_model.search_fetch(domain, [field_name], limit=cap)
        values = np.array(
            [v for v in records.mapped(field_name)
             if isinstance(v, (int, float)) and not isinstance(v, bool)],
            dtype=float)
        return values[np.isfinite(values)]

    @api.model
    def _stats_period_counts(self, records_model, domain, date_field, period):
        """Counter {period_key: number of records} over the matching records."""
        records = records_model.search_fetch(
            list(domain) + [(date_field, "!=", False)], [date_field])
        return Counter(self._period_key(d, period)
                       for d in records.mapped(date_field) if d)

    @api.model
    def _stats_period_ratios(self, records_model, domain, date_field, period,
                             success_domain):
        """Per-period matching/total ratios (e.g. a conversion rate)."""
        total = self._stats_period_counts(
            records_model, domain, date_field, period)
        won = self._stats_period_counts(
            records_model, list(domain) + list(success_domain),
            date_field, period)
        return np.array([won.get(key, 0) / count
                         for key, count in total.items() if count],
                        dtype=float)

    @api.model
    def _ai_tool_statistics(self, model_name, field_name=None, domain=None,
                            mode=None, date_field=None, period=None,
                            success_domain=None):
        """AI tool entry point: precise stats (incl. standard deviation, which
        read_group cannot give) to estimate a Monte Carlo variable from data."""
        mode = mode or ("field" if field_name else "count")
        period = period or "month"
        if model_name not in self.env:
            return {"error": "Unknown model '%s'." % model_name}
        Model = self.env[model_name]
        try:
            dom = self._ai_parse_domain(domain)
        except ValueError as error:
            return {"error": str(error)}
        capped = False
        try:
            if mode == "count":
                if not date_field:
                    return {"error": "date_field is required for mode 'count'."}
                counts = self._stats_period_counts(
                    Model, dom, date_field, period)
                values = np.array(list(counts.values()), dtype=float)
            elif mode == "ratio":
                if not date_field:
                    return {"error": "date_field is required for mode 'ratio'."}
                success = self._ai_parse_domain(success_domain)
                if not success:
                    return {"error": "success_domain is required for mode "
                                     "'ratio' (it selects the numerator)."}
                values = self._stats_period_ratios(
                    Model, dom, date_field, period, success)
            else:
                if not field_name:
                    return {"error": "field_name is required for mode 'field'."}
                values = self._stats_field_values(
                    Model, dom, field_name, cap=self._AI_STATS_RECORD_CAP)
                capped = values.size >= self._AI_STATS_RECORD_CAP
        except Exception as error:  # noqa: BLE001
            return {"error": str(error)}
        if values.size == 0:
            return {"n": 0, "note": "No matching data."}
        result = {
            "n": int(values.size),
            "mean": round(float(values.mean()), 4),
            "std": round(float(values.std()), 4),
            "min": round(float(values.min()), 4),
            "p25": round(float(np.percentile(values, 25)), 4),
            "median": round(float(np.median(values)), 4),
            "p75": round(float(np.percentile(values, 75)), 4),
            "max": round(float(values.max()), 4),
            "mode": mode,
        }
        if capped:
            result["note"] = ("Computed on the first %s matching records."
                              % self._AI_STATS_RECORD_CAP)
        return result

    _AI_TOOL_NAME = "AI: Monte Carlo statistics"

    @api.model
    def _setup_ai_statistics_tool(self):
        """Register the statistics AI tool and attach it to Odoo's Ask-AI
        topic. No-op when the EE 'ai' module is not installed."""
        if "ai.agent" not in self.env or "ai.topic" not in self.env:
            return False
        schema = json.dumps({
            "type": "object",
            "properties": {
                "model_name": {"type": "string", "description":
                    "Technical model name, e.g. 'sale.order', 'crm.lead'."},
                "field_name": {"type": "string", "description":
                    "Numeric field to analyse for mode 'field', e.g. 'amount_total'."},
                "domain": {"type": "string", "description":
                    "Odoo domain as a JSON string to filter records, e.g. "
                    "[[\"state\",\"in\",[\"sale\",\"done\"]]]. Empty list for all."},
                "mode": {"type": "string", "enum": ["field", "count", "ratio"],
                         "description":
                    "'field' = statistics of a numeric field's values; 'count' = "
                    "number of records per period (e.g. leads/month); 'ratio' = "
                    "matching/total per period (e.g. conversion rate)."},
                "date_field": {"type": "string", "description":
                    "Date field to group by per period for 'count'/'ratio', e.g. 'create_date'."},
                "period": {"type": "string",
                           "enum": ["day", "week", "month", "quarter", "year"],
                           "description": "Time bucket for 'count'/'ratio' modes."},
                "success_domain": {"type": "string", "description":
                    "For mode 'ratio': JSON domain selecting the numerator subset, "
                    "e.g. [[\"stage_id.is_won\",\"=\",true]]."},
            },
            "required": ["model_name"],
        })
        description = (
            "**Tool Name: monte_carlo_statistics**\n\n"
            "Compute precise descriptive statistics (count n, mean, standard "
            "deviation, min, P25, median, P75, max) to estimate a Monte Carlo "
            "input from the database. Use this whenever you need the STANDARD "
            "DEVIATION or percentiles, which read_group cannot provide. Modes: "
            "'field' (a numeric field's values), 'count' (records per period, "
            "e.g. number of leads per month), 'ratio' (matching/total per "
            "period, e.g. a conversion rate)."
        )
        code = ("ai['result'] = env['monte.carlo.variable']._ai_tool_statistics("
                "model_name, field_name, domain, mode, date_field, period, "
                "success_domain)")
        Server = self.env["ir.actions.server"].sudo()
        vals = {
            "name": self._AI_TOOL_NAME,
            "model_id": self.env["ir.model"]._get("ai.agent").id,
            "state": "code", "use_in_ai": True, "code": code,
            "ai_tool_description": description, "ai_tool_schema": schema,
        }
        tool = Server.search([("name", "=", self._AI_TOOL_NAME)], limit=1)
        if tool:
            tool.write(vals)
        else:
            tool = Server.with_context(default_use_in_ai=True).create(vals)
        # Give the action a stable external id: Odoo derives the LLM-facing tool
        # name from the xml_id (else a meaningless "action_<id>"), so this makes
        # the agent see it as "monte_carlo_statistics".
        self.env["ir.model.data"]._update_xmlids([{
            "xml_id": "era_monte_carlo_simulation.monte_carlo_statistics",
            "record": tool,
            "noupdate": True,
        }])
        topic = self.env.ref("ai.ai_topic_information_retrieval_query",
                             raise_if_not_found=False)
        if topic:
            topic.sudo().write({"tool_ids": [(4, tool.id)]})
        return tool.id

    def _refresh_from_source(self):
        self.ensure_one()
        if not self.source_model_id:
            raise UserError(_("Choose a Source Model before refreshing."))
        model_name = self.source_model_id.model
        if model_name not in self.env:
            raise UserError(_("Unknown source model '%s'.", model_name))
        try:
            domain = safe_eval(self.source_domain or "[]")
        except Exception as error:  # noqa: BLE001
            raise UserError(_("Invalid Source Filter: %s", error))
        domain = self._apply_lookback(domain)
        if self.source_mode == "count":
            values = self._fetch_period_counts(model_name, domain)
        elif self.source_mode == "ratio":
            values = self._fetch_period_ratios(model_name, domain)
        else:
            if not self.source_field_id:
                raise UserError(_("Choose a Source Field before refreshing."))
            # Read under the user's own rights (no sudo): respect access rules.
            values = self._stats_field_values(
                self.env[model_name], domain, self.source_field_id.name)
        if values.size < 2:
            raise UserError(_(
                "Not enough data to estimate the distribution "
                "(found %s usable value(s)).", int(values.size)))
        if (self.distribution in ("uniform", "triangular")
                and float(values.min()) == float(values.max())):
            raise UserError(_(
                "The source data has no variation; pick a varying field or a "
                "Fixed/Normal distribution."))
        self.write(self._fit_distribution(values))

    def _apply_lookback(self, domain):
        """Append a recent-window date filter when a History Window is set."""
        self.ensure_one()
        years = {"1y": 1, "3y": 3, "5y": 5}.get(self.source_lookback)
        date_field = (self.source_date_field_id.name
                      if self.source_date_field_id else None)
        if not years or not date_field:
            return domain
        cutoff = fields.Date.context_today(self) - relativedelta(years=years)
        return list(domain) + [(date_field, ">=", fields.Date.to_string(cutoff))]

    @staticmethod
    def _period_key(value, period):
        if period == "year":
            return (value.year,)
        if period == "quarter":
            return (value.year, (value.month - 1) // 3)
        if period == "week":
            return tuple(value.isocalendar()[:2])
        if period == "day":
            return (value.year, value.month, value.day)
        return (value.year, value.month)

    def _count_by_period(self, model_name, domain):
        """Return a Counter {period_key: number of records}."""
        self.ensure_one()
        return self._stats_period_counts(
            self.env[model_name], domain, self.source_date_field_id.name,
            self.source_period or "month")

    def _fetch_period_counts(self, model_name, domain):
        """Return the number of records per time bucket (e.g. leads/month)."""
        self.ensure_one()
        if not self.source_date_field_id:
            raise UserError(_(
                "Choose a Date Field to group by for the count."))
        return np.array(
            list(self._count_by_period(model_name, domain).values()),
            dtype=float)

    def _fetch_period_ratios(self, model_name, domain):
        """Return the matching/total ratio per period (e.g. conversion rate)."""
        self.ensure_one()
        if not self.source_date_field_id:
            raise UserError(_(
                "Choose a Date Field to group by for the rate."))
        try:
            success = safe_eval(self.source_success_domain or "[]")
        except Exception as error:  # noqa: BLE001
            raise UserError(_("Invalid Numerator Filter: %s", error))
        if not success:
            raise UserError(_(
                "Set a Numerator Filter that selects the 'success' subset "
                "(e.g. won opportunities)."))
        return self._stats_period_ratios(
            self.env[model_name], domain, self.source_date_field_id.name,
            self.source_period or "month", success)

    def _fit_distribution(self, values):
        """Map an empirical sample to this variable's distribution params.

        The fit is made *robust* and *safe*:

        * Normal is auto-upgraded to Log-normal when the data is strictly
          positive but a Normal fit would put a meaningful mass below zero
          (mean - 2*sd < 0). This prevents impossible negative outcomes such
          as a negative deal value feeding a revenue forecast.
        * Triangular is fitted from the 5th/50th/95th percentiles instead of
          the raw min/max, so a single outlier period (e.g. one freak month)
          no longer blows up the range.
        """
        self.ensure_one()
        vals = {"source_sample_count": int(values.size)}
        dist = self.distribution
        mean = float(values.mean())
        # Sample std (ddof=1): the conventional estimator for an empirical fit.
        std = float(values.std(ddof=1)) if values.size > 1 else 0.0
        nonneg = float(values.min()) >= 0.0
        if dist in ("normal", "lognormal") and std <= 0.0:
            # Constant source data: a Normal/Log-normal with an invented spread
            # would misrepresent it — represent it exactly as a Fixed value
            # (action_refresh_from_source notifies about the switch).
            vals["distribution"] = "fixed"
            vals["fixed_value"] = mean
        elif dist == "normal":
            if nonneg and mean > 0.0 and (mean - 2.0 * std) < 0.0:
                vals["distribution"] = "lognormal"
            vals["mean_value"] = mean
            vals["std_dev"] = std
        elif dist == "lognormal":
            if mean <= 0.0:
                raise UserError(_(
                    "Variable '%s': the source data is not strictly positive, "
                    "so it cannot be fitted to a Log-normal distribution. Use "
                    "Normal or Triangular instead.", self.name))
            vals["mean_value"] = mean
            vals["std_dev"] = std
        elif dist == "uniform":
            vals["min_value"] = float(values.min())
            vals["max_value"] = float(values.max())
        elif dist == "triangular":
            lo, mid, hi = (float(x) for x in np.percentile(values, [5, 50, 95]))
            if lo >= hi:  # tails collapse (little spread): fall back to range
                lo, hi = float(values.min()), float(values.max())
            mid = min(max(mid, lo), hi)
            vals["min_value"], vals["mode_value"], vals["max_value"] = lo, mid, hi
        elif dist == "fixed":
            vals["fixed_value"] = mean
        elif dist == "discrete":
            uniq = sorted({round(float(v), 4) for v in values})[:50]
            vals["discrete_values"] = ", ".join("%g" % v for v in uniq)
        return vals

    # ------------------------------------------------------------------
    # Defaults & validation
    # ------------------------------------------------------------------
    @api.onchange("name")
    def _onchange_name_set_code(self):
        """Suggest a code derived from the name when none is set yet."""
        for record in self:
            if record.name and not record.code:
                record.code = record._slugify_code(record.name)

    @staticmethod
    def _slugify_code(value):
        slug = re.sub(r"[^a-zA-Z0-9_]+", "_", (value or "").strip().lower())
        slug = slug.strip("_")
        if not slug or slug[0].isdigit():
            slug = "var_" + slug
        return slug

    @api.constrains("code", "model_id")
    def _check_code(self):
        for record in self:
            if not record.code or not _CODE_RE.match(record.code):
                raise ValidationError(_(
                    "Code '%s' is invalid. Use letters, digits and "
                    "underscores, and do not start with a digit.",
                    record.code))
            duplicate = self.search_count([
                ("model_id", "=", record.model_id.id),
                ("code", "=", record.code),
                ("id", "!=", record.id),
            ])
            if duplicate:
                raise ValidationError(_(
                    "Code '%s' is already used by another variable in this "
                    "model.", record.code))

    @api.constrains("distribution", "fixed_value", "min_value", "max_value",
                    "mean_value", "std_dev", "mode_value", "discrete_values")
    def _check_distribution_parameters(self):
        for record in self:
            record._validate_parameters()

    def _validate_parameters(self):
        """Raise ValidationError when the distribution parameters are
        inconsistent. Centralised so the engine can rely on clean data."""
        self.ensure_one()
        dist = self.distribution
        if dist == "uniform":
            if self.min_value >= self.max_value:
                raise ValidationError(_(
                    "Variable '%s': uniform distribution requires "
                    "Minimum < Maximum.", self.name))
        elif dist == "normal":
            if self.std_dev <= 0:
                raise ValidationError(_(
                    "Variable '%s': normal distribution requires a positive "
                    "Standard Deviation.", self.name))
        elif dist == "lognormal":
            if self.mean_value <= 0:
                raise ValidationError(_(
                    "Variable '%s': log-normal distribution requires a positive "
                    "Mean (it models a strictly positive quantity).", self.name))
            if self.std_dev <= 0:
                raise ValidationError(_(
                    "Variable '%s': log-normal distribution requires a positive "
                    "Standard Deviation.", self.name))
            # Reject a Std/Mean ratio so extreme that (std/mean)**2 overflows
            # float64 (would raise OverflowError / yield non-finite samples).
            try:
                cv2 = (self.std_dev / self.mean_value) ** 2
            except OverflowError:
                cv2 = float("inf")
            if not math.isfinite(cv2):
                raise ValidationError(_(
                    "Variable '%s': the Std/Mean ratio is too extreme for a "
                    "log-normal distribution; reduce the Standard Deviation.",
                    self.name))
        elif dist == "triangular":
            if not (self.min_value <= self.mode_value <= self.max_value):
                raise ValidationError(_(
                    "Variable '%s': triangular distribution requires "
                    "Minimum <= Mode <= Maximum.", self.name))
            if self.min_value >= self.max_value:
                raise ValidationError(_(
                    "Variable '%s': triangular distribution requires "
                    "Minimum < Maximum.", self.name))
        elif dist == "discrete":
            if not self._parse_discrete_values():
                raise ValidationError(_(
                    "Variable '%s': discrete distribution requires at least "
                    "one numeric value (comma separated).", self.name))

    def _parse_discrete_values(self):
        """Return the configured discrete values as a list of floats."""
        self.ensure_one()
        values = []
        for chunk in (self.discrete_values or "").replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                num = float(chunk)
            except ValueError:
                raise ValidationError(_(
                    "Variable '%s': '%s' is not a valid number in the "
                    "discrete values.", self.name, chunk))
            if not math.isfinite(num):
                raise ValidationError(_(
                    "Variable '%s': '%s' is not a finite number; inf/NaN are "
                    "not allowed.", self.name, chunk))
            values.append(num)
        return values

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------
    def generate_samples(self, iterations, random_state=None):
        """Return a numpy array of ``iterations`` samples for this variable.

        :param int iterations: number of samples to draw.
        :param random_state: optional ``numpy.random.Generator`` so a run can
            be reproducible; falls back to the global generator.
        """
        self.ensure_one()
        self._validate_parameters()
        rng = random_state if random_state is not None else np.random.default_rng()
        dist = self.distribution

        if dist == "fixed":
            return np.full(iterations, self.fixed_value, dtype=float)
        if dist == "uniform":
            return rng.uniform(self.min_value, self.max_value, iterations)
        if dist == "normal":
            return rng.normal(self.mean_value, self.std_dev, iterations)
        if dist == "lognormal":
            # Interpret Mean/Std as the mean and std of the (positive) data and
            # derive the underlying normal parameters (method of moments), so
            # every draw is strictly positive and right-skewed. log1p + a guard
            # keep an extreme Std/Mean ratio from raising OverflowError.
            m, s = self.mean_value, self.std_dev
            try:
                sigma2 = math.log1p((s / m) ** 2)
            except OverflowError:
                raise ValidationError(_(
                    "Variable '%s': the Std/Mean ratio is too extreme for a "
                    "log-normal distribution; reduce the Standard Deviation.",
                    self.name))
            mu = math.log(m) - sigma2 / 2.0
            return rng.lognormal(mu, math.sqrt(sigma2), iterations)
        if dist == "triangular":
            return rng.triangular(
                self.min_value, self.mode_value, self.max_value, iterations)
        if dist == "discrete":
            choices = np.array(self._parse_discrete_values(), dtype=float)
            return rng.choice(choices, size=iterations)

        raise ValidationError(_(
            "Variable '%s': unknown distribution '%s'.", self.name, dist))

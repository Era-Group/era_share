# -*- coding: utf-8 -*-
import ast
import base64
import io
import logging
import math
import re
import time

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval

import numpy as np
import requests
import xlsxwriter

_logger = logging.getLogger(__name__)

# Hard ceiling to protect the database / worker from an accidental huge run.
MAX_ITERATIONS = 1000000

# Helpers exposed to custom expressions. Only plain callables are exposed
# (never the numpy module itself) so safe_eval's value check is satisfied and
# no attribute access is needed inside the expression.
CUSTOM_HELPERS = {
    "min": np.minimum,
    "max": np.maximum,
    "abs": np.abs,
    "where": np.where,
    "exp": np.exp,
    "log": np.log,
    "sqrt": np.sqrt,
}

# Objectives where a LOWER outcome is the success (staying under a budget,
# deadline or safety-stock level). For every other objective a HIGHER outcome
# is the success. Drives the success-probability and the risk verdict.
BELOW_IS_GOOD_OBJECTIVES = ("cost_estimation", "project_duration",
                            "inventory_demand")

# Objectives whose outcome cannot legitimately be negative. A material share
# of negative scenarios here signals a mis-specified input (typically a Normal
# distribution on a strictly positive quantity). Profit and custom are excluded
# because they can genuinely be negative.
NON_NEGATIVE_OBJECTIVES = ("revenue_forecast", "cost_estimation",
                           "project_duration", "inventory_demand")

# Number of histogram bins for the outcome distribution chart.
DISTRIBUTION_BINS = 24

# Name of the dedicated, user-configurable AI agent that drives the run
# narration. Created on install/upgrade; point it at any provider account in the
# AI app and the narration follows it — no code change needed.
MC_NARRATOR_AGENT = "Monte Carlo Narrator"


class MonteCarloRun(models.Model):
    _name = "monte.carlo.run"
    _description = "Monte Carlo Simulation Run"
    _order = "create_date desc, id desc"

    name = fields.Char(
        string="Reference", default=lambda self: _("New"),
        help="Reference for this run. Generated automatically; you can rename it.")
    model_id = fields.Many2one(
        "monte.carlo.model", string="Simulation Model",
        required=True, ondelete="cascade", index=True,
        help="The simulation model executed by this run.")
    iterations = fields.Integer(
        string="Iterations",
        default=lambda self: self.env["monte.carlo.model"].browse(
            self.env.context.get("default_model_id")).default_iterations or 10000,
        help="Number of scenarios to simulate. Each iteration draws one random "
             "value per variable, then applies the formula. More iterations give "
             "more reliable results (default 10,000).")
    seed = fields.Integer(
        string="Random Seed",
        help="Optional. Set a fixed value to make the run reproducible (the same "
             "inputs always give the same results); leave 0 for a fresh random "
             "draw each time. Reproducibility holds as long as the model's "
             "variables and correlations are unchanged — editing either list "
             "shifts the random draws.")

    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("running", "Running"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        string="Status", default="draft", required=True, copy=False,
        help="Lifecycle of the run: Draft (not run yet), Running, Done "
             "(results ready) or Failed (see the Error message).")

    started_at = fields.Datetime(
        string="Started At", readonly=True, copy=False,
        help="When the last run started.")
    finished_at = fields.Datetime(
        string="Finished At", readonly=True, copy=False,
        help="When the last run finished.")
    error_message = fields.Text(
        string="Error", readonly=True, copy=False,
        help="Details of the error, shown when the run failed.")

    result_ids = fields.One2many(
        "monte.carlo.result", "run_id", string="Results",
        help="One row per simulated scenario, storing its output and the input "
             "values used.")
    result_count = fields.Integer(
        string="Result Rows", compute="_compute_result_count",
        help="Number of stored result rows for this run.")

    currency_id = fields.Many2one(
        related="model_id.currency_id", string="Currency", store=True,
        help="Currency used to display monetary results (taken from the model).")
    company_id = fields.Many2one(
        related="model_id.company_id", string="Company", store=True,
        help="Company that owns this run (taken from the model).")

    # Summary statistics ------------------------------------------------
    summary_min = fields.Float(
        string="Minimum", readonly=True, copy=False,
        help="Smallest output across all scenarios (worst observed case).")
    summary_max = fields.Float(
        string="Maximum", readonly=True, copy=False,
        help="Largest output across all scenarios (best observed case).")
    summary_mean = fields.Float(
        string="Mean", readonly=True, copy=False,
        help="Average output across all simulated scenarios.")
    summary_median = fields.Float(
        string="Median", readonly=True, copy=False,
        help="Middle output: half of the scenarios are below this value and "
             "half above.")
    summary_std = fields.Float(
        string="Standard Deviation", readonly=True, copy=False,
        help="How much the output varies between scenarios. A higher value "
             "means more uncertainty and risk.")
    negative_fraction = fields.Float(
        string="% Negative", readonly=True, copy=False,
        help="Share of scenarios whose outcome is below zero. For objectives "
             "that cannot be negative (e.g. revenue) anything above ~0 points "
             "to a mis-specified input distribution.")
    p05 = fields.Float(
        string="P5", readonly=True, copy=False,
        help="5th percentile: only 5% of scenarios fall below this value "
             "(a pessimistic / downside estimate).")
    p25 = fields.Float(
        string="P25", readonly=True, copy=False,
        help="25th percentile: 25% of scenarios fall below this value.")
    p50 = fields.Float(
        string="P50 (Median)", readonly=True, copy=False,
        help="50th percentile (median): half of the scenarios fall below this "
             "value.")
    p75 = fields.Float(
        string="P75", readonly=True, copy=False,
        help="75th percentile: 75% of scenarios fall below this value.")
    p95 = fields.Float(
        string="P95", readonly=True, copy=False,
        help="95th percentile: 95% of scenarios fall below this value "
             "(an optimistic / upside estimate).")

    # Threshold analysis ------------------------------------------------
    success_threshold = fields.Float(
        string="Success Threshold",
        help="A target value to test against. The run reports the probability "
             "of the outcome reaching (>=) and of staying below (<) this value. "
             "For revenue/profit a higher result is better; for cost/duration a "
             "lower result is better.")
    probability_above_threshold = fields.Float(
        string="P(>= threshold) %", readonly=True, copy=False,
        help="Percentage of scenarios where the outcome is greater than or "
             "equal to the Success Threshold.")
    probability_below_threshold = fields.Float(
        string="P(< threshold) %", readonly=True, copy=False,
        help="Percentage of scenarios where the outcome is below the Success "
             "Threshold (e.g. the chance of staying under budget).")

    # Confidence in the estimate ----------------------------------------
    se_mean = fields.Float(
        string="Std. Error (mean)", readonly=True, copy=False,
        help="Standard error of the mean (std / sqrt(N)): the sampling "
             "uncertainty of the average estimate itself. Smaller is better; "
             "it shrinks as you raise the number of iterations.")
    mean_ci_low = fields.Float(
        string="Mean 95% CI Low", readonly=True, copy=False,
        help="Lower bound of the 95% confidence interval for the mean.")
    mean_ci_high = fields.Float(
        string="Mean 95% CI High", readonly=True, copy=False,
        help="Upper bound of the 95% confidence interval for the mean.")

    # Distribution chart ------------------------------------------------
    distribution_data = fields.Json(
        string="Distribution Data", copy=False,
        help="Binned histogram and cumulative curve of the outcomes. Stored "
             "compactly and reused to draw the chart (and later by exports and "
             "run comparisons).")
    distribution_chart = fields.Html(
        string="Distribution Chart", compute="_compute_distribution_chart",
        sanitize=False,
        help="Histogram of the outcomes with the cumulative S-curve and the "
             "percentile / threshold markers.")

    # Tail risk ---------------------------------------------------------
    var_95 = fields.Float(
        string="Value at Risk (95%)", readonly=True, copy=False,
        help="The outcome at the edge of the worst 5% of scenarios "
             "(the 5th percentile for upside objectives, the 95th for cost / "
             "duration / inventory).")
    cvar_95 = fields.Float(
        string="Expected Shortfall (CVaR 95%)", readonly=True, copy=False,
        help="Average outcome across only the worst 5% of scenarios "
             "(conditional value at risk) - the typical severity when things "
             "go badly, which the percentile alone does not show.")

    # Sensitivity (key drivers) -----------------------------------------
    sensitivity_data = fields.Json(
        string="Sensitivity Data", copy=False,
        help="Per-variable rank correlation between each input and the "
             "outcome, used to draw the tornado / key-drivers chart.")
    sensitivity_chart = fields.Html(
        string="Key Drivers Chart", compute="_compute_sensitivity_chart",
        sanitize=False,
        help="Tornado chart ranking which input variables drive the outcome "
             "the most (by rank correlation).")

    # Input correlation (Iman-Conover) ----------------------------------
    correlation_data = fields.Json(
        string="Correlation Data", copy=False,
        help="For each correlation defined on the model, the target rank "
             "correlation requested and the one actually achieved in this run.")
    correlation_summary = fields.Html(
        string="Input Correlations", compute="_compute_correlation_summary",
        sanitize=False,
        help="Table comparing the requested input correlations with the ones "
             "reproduced by the run.")

    interpretation = fields.Html(
        string="Interpretation", compute="_compute_interpretation",
        sanitize=False,
        help="Plain-language summary of what the results mean for the business.")
    ai_interpretation = fields.Html(
        string="AI Summary", readonly=True, copy=False,
        help="Board-ready narrative written by your configured AI provider from "
             "the run's numbers. Click 'Explain with AI' to generate it.")
    ai_pending = fields.Boolean(
        string="AI Summary Pending", default=False, copy=False,
        help="Set right after a run while the AI summary is generated in the "
             "background by a scheduled job.")
    ai_attempts = fields.Integer(
        string="AI Attempts", default=0, copy=False,
        help="How many times the background job has tried to generate the AI "
             "summary (it gives up after a few failures).")

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends("result_ids")
    def _compute_result_count(self):
        result_data = self.env["monte.carlo.result"]._read_group(
            [("run_id", "in", self.ids)],
            groupby=["run_id"], aggregates=["__count"])
        counts = {run.id: count for run, count in result_data}
        for run in self:
            run.result_count = counts.get(run.id, 0)

    @api.depends("state", "summary_mean", "summary_min", "summary_max",
                 "p05", "p95", "success_threshold", "mean_ci_high", "cvar_95",
                 "negative_fraction", "probability_above_threshold",
                 "probability_below_threshold", "sensitivity_data",
                 "correlation_data", "model_id.objective", "se_mean",
                 "iterations", "result_ids")
    def _compute_interpretation(self):
        for run in self:
            if run.state != "done":
                run.interpretation = False
                continue
            run.interpretation = run._build_interpretation()

    @api.depends("distribution_data")
    def _compute_distribution_chart(self):
        for run in self:
            run.distribution_chart = (
                run._render_distribution_svg(run.distribution_data)
                if run.distribution_data else False)

    @api.depends("sensitivity_data")
    def _compute_sensitivity_chart(self):
        for run in self:
            run.sensitivity_chart = (
                run._render_sensitivity_svg(run.sensitivity_data)
                if run.sensitivity_data else False)

    @api.depends("correlation_data")
    def _compute_correlation_summary(self):
        for run in self:
            run.correlation_summary = (
                run._render_correlation_html(run.correlation_data)
                if run.correlation_data else False)

    def _format_amount(self, value):
        """Human, currency-aware formatting for the narrative text."""
        self.ensure_one()
        currency = self.currency_id
        if currency:
            return currency.format(value)
        return "{:,.2f}".format(value)

    def _success_metric(self):
        """Return ``(probability, below_is_good)`` for this run's objective.

        For cost / duration / inventory the success is staying *below* the
        threshold; for revenue / profit / custom it is reaching it.
        """
        self.ensure_one()
        below_good = self.model_id.objective in BELOW_IS_GOOD_OBJECTIVES
        prob = (self.probability_below_threshold if below_good
                else self.probability_above_threshold)
        return prob, below_good

    def _build_interpretation(self):
        self.ensure_one()
        # Amounts embed the currency symbol (user-editable data) and the text
        # is rendered as unsanitised Html, so escape every formatted amount.
        fmt = lambda value: self._svg_escape(self._format_amount(value))  # noqa: E731
        prob, below_good = self._success_metric()
        # Colour-coded risk verdict driven by the success confidence.
        if prob >= 80.0:
            css, level = "success", _("high")
        elif prob >= 50.0:
            css, level = "warning", _("moderate")
        else:
            css, level = "danger", _("low")
        banner = (
            '<div class="alert alert-%s" role="alert" style="margin-bottom:8px;">'
            '%s</div>' % (css, _(
                "Success confidence: %(prob).1f%% (%(level)s)",
                prob=prob, level=level)))

        # Data-quality guard: an outcome that cannot be negative (e.g. revenue)
        # but turns up negative scenarios almost always means an input uses a
        # Normal distribution on a strictly positive quantity. negative_fraction
        # is a percentage; warn past 5% (i.e. the P5 downside is itself below 0).
        if (self.model_id.objective in NON_NEGATIVE_OBJECTIVES
                and self.negative_fraction >= 5.0):
            banner += (
                '<div class="alert alert-danger" role="alert" '
                'style="margin-bottom:8px;">%s</div>' % _(
                    "⚠ %(pct).1f%% of scenarios are negative, which is normally "
                    "impossible for this objective. This usually means an input "
                    "uses a Normal distribution on a positive quantity — switch "
                    "it to Log-normal or Triangular, or click 'Refresh from "
                    "data' (it now fits a safe positive distribution "
                    "automatically).", pct=self.negative_fraction))

        lines = [
            _("Average expected outcome across all %(n)s simulated scenarios "
              "is %(mean)s.",
              n="{:,}".format(self.result_count or self.iterations),
              mean=fmt(self.summary_mean)),
            _("The average is statistically reliable to within ±%(err)s "
              "(95%% confidence).",
              err=fmt(1.96 * self.se_mean)),
            _("In 90%% of scenarios the outcome falls between %(low)s and "
              "%(high)s.",
              low=fmt(self.p05),
              high=fmt(self.p95)),
        ]
        # A threshold of exactly 0 (e.g. profit >= 0) is a valid threshold.
        if below_good:
            lines.append(_(
                "There is a %(prob).1f%% chance the outcome stays within the "
                "threshold of %(thr)s.",
                prob=self.probability_below_threshold,
                thr=fmt(self.success_threshold)))
        else:
            lines.append(_(
                "There is a %(prob).1f%% chance the outcome reaches at least "
                "the success threshold of %(thr)s.",
                prob=self.probability_above_threshold,
                thr=fmt(self.success_threshold)))
        lines.append(_(
            "In the worst 5%% of scenarios the outcome averages %(cvar)s "
            "(expected shortfall).",
            cvar=fmt(self.cvar_95)))
        drivers = (self.sensitivity_data or {}).get("drivers") or []
        if drivers and abs(drivers[0]["corr"]) >= 0.05:
            top = drivers[0]
            # Variable names are user input and this text is rendered as
            # unsanitised Html, so escape them before interpolation.
            lines.append(_(
                "%(name)s is the biggest driver of the outcome "
                "(rank correlation %(corr).2f).",
                name=self._svg_escape(top["name"]), corr=top["corr"]))
        pairs = (self.correlation_data or {}).get("pairs") or []
        if pairs:
            strongest = max(pairs, key=lambda p: abs(p["target"]))
            lines.append(_(
                "Reproduced %(n)s input correlation(s); the strongest links "
                "%(v1)s and %(v2)s (target %(t).2f, achieved %(a).2f).",
                n=len(pairs), v1=self._svg_escape(strongest["v1"]),
                v2=self._svg_escape(strongest["v2"]),
                t=strongest["target"], a=strongest["achieved"]))
        return banner + "<ul>%s</ul>" % "".join(
            "<li>%s</li>" % line for line in lines)

    @api.constrains("seed")
    def _check_seed(self):
        # numpy's Generator rejects negative seeds with a cryptic ValueError;
        # validate here so the user gets a clear message instead of a failed run.
        for run in self:
            if run.seed < 0:
                raise ValidationError(_(
                    "The Random Seed must be 0 or a positive integer."))

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        # Seed a per-model counter once, then increment locally so runs created
        # together in one batch for the same model get distinct numbers.
        counters = {}
        for vals in vals_list:
            model = self.env["monte.carlo.model"].browse(vals.get("model_id"))
            if model and not vals.get("iterations"):
                vals["iterations"] = model.default_iterations or 10000
            if model and (not vals.get("name") or vals["name"] == _("New")):
                counters.setdefault(model.id, len(model.run_ids))
                counters[model.id] += 1
                vals["name"] = _("%(model)s - Run #%(num)s",
                                 model=model.name, num=counters[model.id])
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_run_simulation(self):
        """Generate samples, evaluate the model formula and store results.

        The whole computation is done in memory first; database writes only
        happen once everything succeeded, so a failure leaves a clean
        ``failed`` state with an explanatory message instead of partial data.
        """
        for run in self:
            run._run_one()
        return True

    def _run_one(self):
        self.ensure_one()
        self._clear_results_and_summary()
        self.write({
            "state": "running",
            "started_at": fields.Datetime.now(),
            "error_message": False,
        })
        try:
            # The whole engine body runs inside a savepoint: a database-level
            # error (statement timeout, insert failure, ...) rolls back to a
            # healthy transaction — including any result rows already stored —
            # so _mark_failed below can record a clean 'failed' state instead
            # of raising 'current transaction is aborted'.
            with self.env.cr.savepoint():
                # Pre-flight checks live INSIDE the try so a mis-configured run
                # is marked 'failed' (and a batch keeps going) instead of
                # aborting the whole loop with an uncaught error.
                if self.iterations < 1 or self.iterations > MAX_ITERATIONS:
                    raise UserError(_(
                        "Iterations must be between 1 and %s.", MAX_ITERATIONS))
                if not self.model_id.variable_ids:
                    raise UserError(_(
                        "Add at least one variable before running the "
                        "simulation."))
                samples = self._generate_samples()
                outputs = self._calculate_output(samples)
                outputs = np.asarray(outputs, dtype=float).reshape(-1)
                if outputs.shape[0] != self.iterations:
                    # A constant expression broadcasts to a single value.
                    outputs = np.broadcast_to(
                        outputs, (self.iterations,)).astype(float)
                if not np.all(np.isfinite(outputs)):
                    raise UserError(_(
                        "The formula produced non-finite values (inf/NaN). "
                        "Please review the variables and expression."))

                summary = self._calculate_summary(outputs)
                # Outputs can each be finite yet their variance/sum overflow to
                # inf (e.g. magnitudes near 1e154). Don't persist non-finite
                # stats.
                bad = [k for k, v in summary.items()
                       if isinstance(v, float) and not math.isfinite(v)]
                if bad:
                    raise UserError(_(
                        "The results are too large to summarise (overflow in: "
                        "%s). Reduce the scale of the inputs or the formula.",
                        ", ".join(sorted(bad))))
                summary["sensitivity_data"] = self._calculate_sensitivity(
                    samples, outputs)
                summary["correlation_data"] = self._correlation_report(samples)
                # Store the (potentially very many) result rows only after the
                # summary validated, so a failed run never keeps partial data.
                self._store_results(outputs, samples)
                summary.update({
                    "state": "done",
                    "finished_at": fields.Datetime.now(),
                })
                self.write(summary)
        except UserError as error:
            # Keep the partial transaction but record the failure clearly.
            self._mark_failed(str(error))
        except Exception as error:  # noqa: BLE001 - surface engine errors safely
            _logger.exception("Monte Carlo run %s failed", self.id)
            self._mark_failed(str(error))
        if self.state == "done":
            self._schedule_ai_summary()
        return True

    def _mark_failed(self, message):
        self.ensure_one()
        # A failed run must never expose partial result rows (the savepoint in
        # _run_one already rolled back rows stored in the same attempt; this
        # also covers any failure path outside it).
        if self.result_ids:
            self.result_ids.sudo().unlink()
        self.write({
            "state": "failed",
            "error_message": message,
            "finished_at": fields.Datetime.now(),
        })

    def action_reset_to_draft(self):
        for run in self:
            run._clear_results_and_summary()
            run.write({
                "state": "draft",
                "started_at": False,
                "finished_at": False,
                "error_message": False,
            })
        return True

    def action_open_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Results - %s", self.name),
            "res_model": "monte.carlo.result",
            "view_mode": "list,graph,pivot",
            "domain": [("run_id", "=", self.id)],
            "context": {"default_run_id": self.id, "search_default_run_id": self.id},
        }

    def action_export_xlsx(self):
        """Build a board-ready Excel workbook (summary + distribution + a sample
        of the raw results) and return it as a download."""
        self.ensure_one()
        if self.state != "done":
            raise UserError(_("Run the simulation before exporting it."))
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        f_title = wb.add_format({"bold": True, "font_size": 14})
        f_hdr = wb.add_format({"bold": True, "bg_color": "#14385f",
                               "font_color": "white", "border": 1})
        f_lbl = wb.add_format({"bold": True, "bg_color": "#f4f8fb", "border": 1})
        f_cell = wb.add_format({"border": 1})
        f_money = wb.add_format({"border": 1, "num_format": "#,##0.00"})
        f_pct = wb.add_format({"border": 1, "num_format": "0.0%"})

        def sheet(name):
            return wb.add_worksheet(name[:31])

        objective = dict(self.model_id._fields["objective"]._description_selection(
            self.env)).get(self.model_id.objective, self.model_id.objective)
        success_prob, below_good = self._success_metric()

        # --- Summary ---
        ws = sheet(_("Summary"))
        ws.set_column(0, 0, 34)
        ws.set_column(1, 1, 22)
        ws.write(0, 0, self.name or _("Simulation"), f_title)
        row = 2
        for key, val in ((_("Model"), self.model_id.name),
                         (_("Objective"), objective),
                         (_("Iterations"), self.iterations),
                         (_("Currency"), self.currency_id.name or "")):
            ws.write(row, 0, key, f_lbl)
            ws.write(row, 1, val, f_cell)
            row += 1
        row += 1
        ws.write(row, 0, _("Key figures"), f_hdr)
        ws.write(row, 1, "", f_hdr)
        row += 1
        figures = [
            (_("Mean (expected)"), self.summary_mean, f_money),
            (_("Median (P50)"), self.p50, f_money),
            (_("Std. deviation"), self.summary_std, f_money),
            (_("Minimum"), self.summary_min, f_money),
            (_("Maximum"), self.summary_max, f_money),
            (_("P5"), self.p05, f_money), (_("P25"), self.p25, f_money),
            (_("P75"), self.p75, f_money), (_("P95"), self.p95, f_money),
            (_("Value at Risk (95%)"), self.var_95, f_money),
            (_("Expected shortfall (CVaR 95%)"), self.cvar_95, f_money),
            (_("Success threshold"), self.success_threshold, f_money),
            # Orientation-aware, like the on-screen banner: for cost/duration/
            # inventory objectives success means staying BELOW the threshold.
            (_("Chance of staying within threshold") if below_good
             else _("Chance of reaching threshold"),
             (success_prob or 0.0) / 100.0, f_pct),
            (_("% negative"), (self.negative_fraction or 0.0) / 100.0, f_pct),
        ]
        for key, val, fmt in figures:
            ws.write(row, 0, key, f_lbl)
            ws.write(row, 1, val or 0.0, fmt)
            row += 1
        drivers = (self.sensitivity_data or {}).get("drivers") or []
        if drivers:
            row += 1
            ws.write(row, 0, _("Key drivers (rank correlation)"), f_hdr)
            ws.write(row, 1, "", f_hdr)
            row += 1
            for drv in drivers:
                ws.write(row, 0, drv.get("name"), f_lbl)
                ws.write(row, 1, round(drv.get("corr", 0.0), 3), f_cell)
                row += 1

        pairs = (self.correlation_data or {}).get("pairs") or []
        if pairs:
            row += 1
            ws.write(row, 0, _("Input correlations (target / achieved)"), f_hdr)
            ws.write(row, 1, "", f_hdr)
            row += 1
            for pair in pairs:
                ws.write(row, 0, "%s / %s" % (pair["v1"], pair["v2"]), f_lbl)
                ws.write(row, 1, "%+.2f / %+.2f" % (
                    pair["target"], pair["achieved"]), f_cell)
                row += 1

        # --- Distribution (+ chart) ---
        bins = (self.distribution_data or {}).get("bins") or []
        if bins:
            dname = _("Distribution")
            wsd = sheet(dname)
            for col, head in enumerate(
                    (_("From"), _("To"), _("Count"), _("Cumulative %"))):
                wsd.write(0, col, head, f_hdr)
            for i, b in enumerate(bins, 1):
                wsd.write(i, 0, b["x0"], f_money)
                wsd.write(i, 1, b["x1"], f_money)
                wsd.write(i, 2, b["count"], f_cell)
                wsd.write(i, 3, b["cdf"], f_pct)
            wsd.set_column(0, 3, 16)
            chart = wb.add_chart({"type": "column"})
            chart.add_series({
                "name": _("Frequency"),
                "categories": [dname[:31], 1, 0, len(bins), 0],
                "values": [dname[:31], 1, 2, len(bins), 2]})
            chart.set_title({"name": _("Outcome distribution")})
            chart.set_legend({"none": True})
            wsd.insert_chart("F2", chart, {"x_scale": 1.7, "y_scale": 1.4})

        # --- Results sample (capped; empty in summary-storage mode) ---
        # Fetch only the exported rows: reading result_ids would first load the
        # ids of every stored row (up to MAX_ITERATIONS in full mode).
        rows = self.env["monte.carlo.result"].search(
            [("run_id", "=", self.id)], order="iteration", limit=10000)
        if rows:
            wsr = sheet(_("Results"))
            codes = list((rows[0].input_snapshot or {}).keys())
            for col, head in enumerate([_("Iteration"), _("Output")] + codes):
                wsr.write(0, col, head, f_hdr)
            for i, res in enumerate(rows, 1):
                wsr.write(i, 0, res.iteration, f_cell)
                wsr.write(i, 1, res.output_value, f_money)
                snap = res.input_snapshot or {}
                for col, code in enumerate(codes, 2):
                    wsr.write(i, col, snap.get(code, ""), f_cell)
            wsr.set_column(0, 1, 14)

        wb.close()
        clean = re.sub(r"[^\w\- ]+", "", self.name or "simulation")[:80].strip()
        attachment = self.env["ir.attachment"].create({
            "name": "%s.xlsx" % (clean or "simulation"),
            "type": "binary",
            "datas": base64.b64encode(output.getvalue()),
            "res_model": "monte.carlo.run",
            "res_id": self.id,
            "mimetype": "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet",
        })
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true" % attachment.id,
            "target": "self",
        }

    def action_explain_with_ai(self):
        """Generate a board-ready narrative via the configured AI provider.

        Sends only the aggregated statistics (never raw iteration rows) and
        degrades gracefully to a note if no provider is configured/reachable.
        """
        self.ensure_one()
        if self.state != "done":
            raise UserError(_(
                "Run the simulation before generating an AI summary."))
        content = self._call_ai_provider(self._build_ai_prompt())
        if content:
            self.write({
                "ai_interpretation": self._format_ai_html(content),
                "ai_pending": False, "ai_attempts": 0,
            })
        else:
            self.write({
                # Clear the pending flag too: a failed manual attempt must not
                # strand the form on the disabled 'Generating...' spinner.
                "ai_pending": False,
                "ai_interpretation": '<p class="text-muted">%s</p>' % _(
                    "AI narration is unavailable right now (the AI provider is "
                    "not configured or could not be reached). The summary above "
                    "still explains the results."),
            })
        return True

    def _schedule_ai_summary(self):
        """Queue background AI-summary generation for this finished run."""
        self.ensure_one()
        if self.env.context.get("mc_skip_ai_summary"):
            return  # e.g. quick one-click simulations don't need an AI summary
        try:
            param = self.env["ir.config_parameter"].sudo()
            if param.get_param(
                    "era_monte_carlo_simulation.ai_auto", "True") in (
                    "False", "0", "false"):
                return
            if not self._ai_available():
                return  # no provider configured -> nothing to generate
            cron = self.env.ref(
                "era_monte_carlo_simulation.ir_cron_generate_ai",
                raise_if_not_found=False)
            if not cron:
                return  # no job to pick it up -> don't strand ai_pending=True
            self.write({"ai_pending": True, "ai_attempts": 0})
            cron.sudo()._trigger()
        except Exception:  # noqa: BLE001 - never break a successful run
            _logger.warning(
                "Could not schedule AI summary for run %s", self.id,
                exc_info=True)

    @api.model
    def _cron_generate_ai_summaries(self, limit=20):
        """Background job: fill the AI summary of runs that requested one."""
        runs = self.search(
            [("ai_pending", "=", True), ("state", "=", "done")], limit=limit)
        for run in runs:
            lang = run.create_uid.lang or self.env.user.lang or "en_US"
            run_lang = run.with_context(lang=lang)
            try:
                content = run_lang._call_ai_provider(run_lang._build_ai_prompt())
            except Exception:  # noqa: BLE001
                _logger.warning(
                    "AI summary generation failed for run %s", run.id,
                    exc_info=True)
                content = None
            if content:
                run.write({
                    "ai_interpretation": run._format_ai_html(content),
                    "ai_pending": False, "ai_attempts": 0,
                })
            else:
                attempts = run.ai_attempts + 1
                run.write({
                    "ai_attempts": attempts,
                    "ai_pending": attempts < 5,
                })
            # Persist each run independently so one slow/failed call keeps the
            # others' progress.
            self.env.cr.commit()
        return True

    @api.autovacuum
    def _gc_keep_ai_cron_active(self):
        """Opt-in self-heal for non-production (neutralized) databases.

        Every deploy neutralises a staging copy, which disables all scheduled
        actions except the autovacuum job. When the operator explicitly opts in
        with the ``ai_auto_even_neutralized`` parameter, re-enable the AI-summary
        cron from here (autovacuum survives neutralization) so background
        generation keeps working on that database too. Default: do nothing -
        neutralization is respected.
        """
        param = self.env["ir.config_parameter"].sudo()
        if param.get_param(
                "era_monte_carlo_simulation.ai_auto_even_neutralized") not in (
                "1", "True", "true"):
            return
        cron = self.env.ref(
            "era_monte_carlo_simulation.ir_cron_generate_ai",
            raise_if_not_found=False)
        if cron and not cron.active:
            cron.sudo().write({"active": True})

    # ------------------------------------------------------------------
    # AI narration (uses the configured custom LLM provider, if any)
    # ------------------------------------------------------------------
    def _ai_provider_config(self):
        """Read the OpenAI-compatible provider settings (shared ir params)."""
        param = self.env["ir.config_parameter"].sudo()
        models = [param.get_param("ai.custom_llm_model"),
                  param.get_param("ai.custom_llm_model_2"),
                  param.get_param("ai.custom_llm_model_3"),
                  param.get_param("ai.custom_llm_model_4")]
        return {
            "key": param.get_param("ai.custom_llm_key"),
            "base_url": param.get_param(
                "ai.custom_llm_base_url", "https://openrouter.ai/api/v1"),
            "models": [m for m in models if m],
            "auth_header": param.get_param(
                "ai.custom_llm_auth_header", "Authorization"),
            "auth_prefix": param.get_param("ai.custom_llm_auth_prefix", "Bearer"),
            "referer": param.get_param("ai.custom_llm_referer"),
            "title": param.get_param("ai.custom_llm_title"),
        }

    def _build_ai_prompt(self):
        """Return chat messages built from aggregate statistics only.

        The dashboard already shows the raw stats, so the prompt asks the model
        for decision-grade *insight* (verdict, practical meaning, one action) in
        very few words - not a restatement of the numbers.
        """
        self.ensure_one()
        lang = self.env.context.get("lang") or "en"
        language = "Arabic" if lang.startswith("ar") else "English"
        currency = self.currency_id.name or self.currency_id.symbol or ""
        prob, below_good = self._success_metric()
        drivers = (self.sensitivity_data or {}).get("drivers", [])[:3]
        drivers_txt = ", ".join(
            "%s (%.2f)" % (d["name"], d["corr"]) for d in drivers) or "unknown"
        spread = (self.summary_mean and self.summary_std / self.summary_mean) or 0.0
        question = (self.model_id.description or "").strip() or self.model_id.name
        facts = (
            "Business question: %s\n"
            "Objective: %s (success = %s outcome than the target)\n"
            "Currency: %s (always use this currency; never assume USD)\n"
            "Scenarios: %s\n"
            "Mean: %.0f | Median: %.0f | Relative spread (std/mean): %.0f%%\n"
            "P5: %.0f | P95: %.0f | Target: %.0f\n"
            "Chance of success: %.1f%%\n"
            "Worst-5%% average outcome (CVaR): %.0f\n"
            "Most influential inputs (rank correlation): %s"
        ) % (question, self.model_id.objective or "",
             "a lower" if below_good else "a higher", currency,
             self.result_count or self.iterations,
             self.summary_mean, self.summary_median, spread * 100.0,
             self.p05, self.p95, self.success_threshold, prob, self.cvar_95,
             drivers_txt)
        system = (
            "You are a sharp business decision advisor. A dashboard ALREADY "
            "shows the mean, percentiles, probabilities and tail risk, so do "
            "NOT restate those numbers. Give a crisp decision takeaway written "
            "in %s only: (a) the verdict (proceed / be cautious / too risky) "
            "and why, (b) what the spread and worst case mean in practice, and "
            "(c) ONE concrete action naming the most influential input. At most "
            "3 short bullets, about 15 words each, under 80 words total. Cite a "
            "number only if it changes the decision. Do NOT include any "
            "reasoning, analysis, planning or preamble. Output ONLY the final "
            "answer between <answer> and </answer>, each bullet on its own line "
            "starting with '- '. Put nothing else inside the tags."
        ) % language
        return [
            {"role": "system", "content": system},
            {"role": "user", "content":
             "Context for your reasoning (do NOT repeat these figures back):\n"
             + facts},
        ]

    @api.model
    def _mc_narrator_agent(self):
        """Return the dedicated 'Monte Carlo Narrator' ai.agent, or False.

        This agent is the single, user-controlled place to choose the provider
        for run narration: open it in the AI app and set its AI Account / model;
        the narration follows it with no code change.
        """
        if "ai.agent" not in self.env:
            return False
        Agent = self.env["ai.agent"].sudo()
        param = self.env["ir.config_parameter"].sudo()
        agent_id = int(param.get_param(
            "era_monte_carlo_simulation.narrator_agent_id") or 0)
        if agent_id:
            agent = Agent.browse(agent_id).exists()
            if agent:
                return agent
        agent = Agent.search([("name", "=", MC_NARRATOR_AGENT)], limit=1)
        if agent:
            param.set_param(
                "era_monte_carlo_simulation.narrator_agent_id", agent.id)
        return agent or False

    @api.model
    def _mc_ensure_narrator_agent(self):
        """Create the dedicated narrator agent if missing (idempotent).

        No-op when the AI app or a usable account is absent. Called from the
        install hook and the upgrade migration; safe to call repeatedly.
        """
        if "ai.agent" not in self.env or "era.ai.account" not in self.env:
            return False
        agent = self._mc_narrator_agent()
        if agent:
            self._mc_register_narrator_xmlids(agent)
            return agent
        Account = self.env["era.ai.account"].sudo()
        account = (Account._resolve_for_user(self.env.user)
                   or Account.search([("active", "=", True),
                                      ("kill_switch", "=", False)], limit=1))
        if not account:
            return False  # nothing to point it at yet
        Agent = self.env["ai.agent"].sudo()
        selection = dict(
            Agent._fields["llm_model"]._description_selection(self.env))
        llm_model = ("custom_llm/custom" if "custom_llm/custom" in selection
                     else next(iter(selection), False))
        try:
            # Wrap in a savepoint so a failed insert rolls back cleanly —
            # including the utm.source, which must not be orphaned — instead
            # of leaving the -u transaction aborted (which kills the whole
            # registry load). The explicit context defaults guard against a
            # required res.partner field (purchase_stock's group_rfq / group_on)
            # not getting its default in the agent->partner creation path.
            with self.env.cr.savepoint():
                vals = {
                    "name": MC_NARRATOR_AGENT,
                    "response_style": "analytical",
                    "llm_model": llm_model,
                    "source_id": self.env["utm.source"].sudo().create(
                        {"name": MC_NARRATOR_AGENT}).id,
                    "era_account_id": account.id,
                }
                record = account._default_chat_model_record()
                if record:
                    vals["era_model_id"] = record.id
                agent = Agent.with_context(
                    default_group_rfq="default", default_group_on="default",
                ).create(vals)
        except Exception:  # noqa: BLE001 - never break install/upgrade on this
            _logger.warning(
                "Could not create the Monte Carlo Narrator agent", exc_info=True)
            return False
        self.env["ir.config_parameter"].sudo().set_param(
            "era_monte_carlo_simulation.narrator_agent_id", agent.id)
        self._mc_register_narrator_xmlids(agent)
        return agent

    @api.model
    def _mc_register_narrator_xmlids(self, agent):
        """Give the narrator agent (and its utm.source) module xml ids so a
        module uninstall cleans them up like any other module data. Idempotent;
        also backfills agents created before this was added."""
        try:
            entries = [{
                "xml_id": "era_monte_carlo_simulation.mc_narrator_agent",
                "record": agent, "noupdate": True,
            }]
            if agent.source_id:
                entries.append({
                    "xml_id": "era_monte_carlo_simulation.mc_narrator_source",
                    "record": agent.source_id, "noupdate": True,
                })
            self.env["ir.model.data"]._update_xmlids(entries)
        except Exception:  # noqa: BLE001 - cosmetic; never break install
            _logger.warning(
                "Could not register xml ids for the narrator agent",
                exc_info=True)

    def _mc_ai_target(self):
        """Resolve ``(account, model)`` for narration.

        Prefers the dedicated narrator agent's account/model (so switching
        provider is a UI edit on that agent); falls back to any usable account.
        """
        self.ensure_one()
        agent = self._mc_narrator_agent()
        if agent and agent.era_account_id:
            account = agent.era_account_id
            model = (agent.era_model_id.model_id if agent.era_model_id
                     else account._default_chat_model())
            return account, model
        account = self._resolve_ai_account()
        if account:
            return account, account._default_chat_model()
        return False, False

    def _resolve_ai_account(self):
        """Return a usable era.ai.account, or an empty/False value.

        Resolves for the run's CREATOR first, then the current user: the
        background AI-summary cron runs as OdooBot, which is usually not allowed
        to use a shared account scoped to specific people, but the human who
        created the run is — so prefer them.
        """
        self.ensure_one()
        if "era.ai.account" not in self.env:
            return False
        Account = self.env["era.ai.account"].sudo()
        for user in (self.create_uid, self.env.user):
            if user and user.id:
                account = Account._resolve_for_user(user)
                if account:
                    return account
        return Account.browse()

    def _ai_available(self):
        """True when some AI provider can be used: the narrator agent / a
        connected era.ai.account (CLI proxy or API key — no custom_llm key
        needed) or the legacy OpenAI-compatible custom_llm settings."""
        self.ensure_one()
        if self._mc_ai_target()[0]:
            return True
        cfg = self._ai_provider_config()
        return bool(cfg["key"] and cfg["models"])

    def _call_ai_provider(self, messages, deadline=60.0):
        """Generate the narrative, preferring the connected AI-accounts stack.

        First tries a resolved ``era.ai.account`` through Odoo's own
        ``LLMApiService`` (so it uses whatever transport is configured — the
        Claude CLI proxy needs no API key); falls back to the legacy
        OpenAI-compatible HTTP call. Returns the text, or None if neither works.
        """
        self.ensure_one()
        text = self._call_via_ai_accounts(messages)
        if text:
            return text
        return self._call_ai_provider_http(messages, deadline=deadline)

    def _call_via_ai_accounts(self, messages):
        """Run the prompt through a connected era.ai.account via LLMApiService.

        Returns the generated text, or None when the AI-accounts stack is not
        installed, no account resolves, or the call fails (so the caller falls
        back to the HTTP path). No hard dependency on the ``ai`` module: the
        import is lazy and guarded.
        """
        self.ensure_one()
        account, model = self._mc_ai_target()
        if not account:
            return None
        try:
            from odoo.addons.ai.utils.llm_api_service import LLMApiService
            system = "\n\n".join(
                m["content"] for m in messages
                if m.get("role") == "system" and m.get("content"))
            user = "\n\n".join(
                m["content"] for m in messages
                if m.get("role") != "system" and m.get("content"))
            svc_env = self.with_context(era_ai_account_id=account.id).env
            service = LLMApiService(
                env=svc_env, provider=account._service_provider())
            responses = service.request_llm(
                model,
                [system] if system else [],
                [],
                inputs=[{"role": "user", "content": user}],
                temperature=0.4)
            if isinstance(responses, (list, tuple)):
                text = " ".join(r for r in responses if isinstance(r, str))
            else:
                text = responses or ""
            return text.strip() or None
        except Exception:  # noqa: BLE001 - fall back to the HTTP provider
            _logger.warning(
                "MC AI narration via era.ai.account failed for run %s; falling "
                "back to the HTTP provider", self.id, exc_info=True)
            return None

    def _call_ai_provider_http(self, messages, deadline=60.0):
        """Call the OpenAI-compatible provider within an overall time budget.

        ``deadline`` caps the TOTAL seconds spent across all model attempts
        (default 60s) so the background job can never run away. Returns the
        text, or None on failure / timeout.
        """
        self.ensure_one()
        cfg = self._ai_provider_config()
        if not cfg["key"] or not cfg["models"]:
            return None
        url = (cfg["base_url"] or "").rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        prefix = cfg["auth_prefix"]
        auth_value = "%s %s" % (prefix, cfg["key"]) if prefix else cfg["key"]
        headers = {"Content-Type": "application/json",
                   cfg["auth_header"] or "Authorization": auth_value}
        if cfg["referer"]:
            headers["HTTP-Referer"] = cfg["referer"]
        if cfg["title"]:
            headers["X-Title"] = cfg["title"]
        start = time.monotonic()
        for model in cfg["models"]:
            remaining = deadline - (time.monotonic() - start)
            if remaining <= 1.0:
                break  # overall time budget exhausted
            try:
                response = requests.post(url, headers=headers, json={
                    "model": model, "messages": messages,
                    "temperature": 0.4, "max_tokens": 400,
                    # Ask reasoning models (via OpenRouter) to drop their chain
                    # of thought from the response; ignored by other providers.
                    "reasoning": {"exclude": True},
                }, timeout=remaining)
                response.raise_for_status()
                choices = (response.json() or {}).get("choices") or []
                content = (choices[0].get("message", {}).get("content")
                           if choices else None)
                if content and content.strip():
                    return content.strip()
            except Exception:  # noqa: BLE001 - never break the UI on provider issues
                _logger.warning(
                    "AI narration: model %s failed", model, exc_info=True)
                continue
        return None

    @staticmethod
    def _format_ai_html(content, max_words=200):
        """Normalise the model output to a clean, capped bulleted list.

        Robust against chatty/reasoning models: drops <think> blocks and keeps
        only the text inside <answer>...</answer> when present, then renders at
        most a handful of bullets within ``max_words``.
        """
        text = (content or "").strip()
        if not text:
            return text
        # Drop code fences and any reasoning-model "thinking" block.
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        text = re.sub(r"(?is)<think>.*?</think>", " ", text)
        # Keep only the delimited final answer if the model provided one
        # (this strips any leaked reasoning / preamble before it).
        match = re.search(r"(?is)<answer>(.*?)</answer>", text)
        if match:
            text = match.group(1)
        elif re.search(r"(?i)<answer>", text):
            text = re.split(r"(?i)<answer>", text, 1)[1]

        def decode(value):
            return (value.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&nbsp;", " "))

        # Extract bullet texts: from <li> if present, else from text lines.
        items = re.findall(r"(?is)<li[^>]*>(.*?)</li>", text)
        if items:
            bullets = [re.sub(r"<[^>]+>", " ", x) for x in items]
        else:
            flat = re.sub(r"(?i)</(p|div|h[1-6])\s*>|<br\s*/?>", "\n", text)
            flat = re.sub(r"<[^>]+>", " ", flat)
            lines = [l.strip() for l in decode(flat).split("\n") if l.strip()]
            marked = [l for l in lines
                      if re.match(r"^\s*([-•*]|\d+[.)])\s+", l)]
            if marked:
                bullets = marked
            elif len(lines) <= 1:
                single = lines[0] if lines else flat
                bullets = re.split(r"(?:^|\s)[-•*]\s+", single)
            else:
                bullets = lines

        def esc(value):
            return (value.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))

        # Clean, then cap to a word budget and a sane number of bullets.
        out, used = [], 0
        for bullet in bullets:
            bullet = re.sub(r"^\s*([-•*]|\d+[.)])\s*", "", decode(bullet))
            bullet = re.sub(r"\s+", " ", bullet).strip()
            if not bullet:
                continue
            words = len(bullet.split())
            if out and used + words > max_words:
                break
            out.append(bullet)
            used += words
            if len(out) >= 8:
                break
        return "<ul>%s</ul>" % "".join("<li>%s</li>" % esc(b) for b in out) if out else ""

    # ------------------------------------------------------------------
    # Engine
    # ------------------------------------------------------------------
    def _clear_results_and_summary(self):
        self.ensure_one()
        if self.result_ids:
            # Results are read-only for plain users; the engine owns them.
            self.result_ids.sudo().unlink()
        self.write({
            "summary_min": 0.0, "summary_max": 0.0, "summary_mean": 0.0,
            "summary_median": 0.0, "summary_std": 0.0, "negative_fraction": 0.0,
            "p05": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0,
            "probability_above_threshold": 0.0,
            "probability_below_threshold": 0.0,
            "se_mean": 0.0, "mean_ci_low": 0.0, "mean_ci_high": 0.0,
            "var_95": 0.0, "cvar_95": 0.0,
            "distribution_data": False, "sensitivity_data": False,
            "correlation_data": False,
            "ai_interpretation": False, "ai_pending": False, "ai_attempts": 0,
        })

    def _generate_samples(self):
        """Return ``{variable_code: numpy.ndarray}`` for every input.

        Each input is drawn independently from its own distribution, then the
        configured input correlations (if any) are imposed by reordering the
        draws (Iman-Conover) — which leaves every marginal distribution intact.
        """
        self.ensure_one()
        rng = np.random.default_rng(self.seed or None)
        samples = {}
        for variable in self.model_id.variable_ids:
            samples[variable.code] = variable.generate_samples(
                self.iterations, random_state=rng)
        self._apply_correlations(samples, rng)
        return samples

    def _calculate_output(self, samples):
        """Apply the model formula to the sampled inputs (vectorised)."""
        self.ensure_one()
        formula_type = self.model_id.formula_type
        if formula_type == "simple_revenue":
            self._require_codes(
                samples, ["leads", "conversion_rate", "average_deal_value"])
            return (samples["leads"] * samples["conversion_rate"]
                    * samples["average_deal_value"])
        if formula_type == "profit":
            self._require_codes(samples, ["units", "unit_price", "unit_cost"])
            fixed_cost = samples.get("fixed_cost", 0.0)
            return (samples["units"]
                    * (samples["unit_price"] - samples["unit_cost"])
                    - fixed_cost)
        if formula_type == "custom_python_limited":
            return self._eval_custom_formula(samples)
        raise UserError(_("Unknown formula type '%s'.", formula_type))

    def _require_codes(self, samples, codes):
        missing = [code for code in codes if code not in samples]
        if missing:
            raise UserError(_(
                "The '%(formula)s' formula needs variables with codes: "
                "%(codes)s. Missing: %(missing)s.",
                formula=self.model_id.formula_type,
                codes=", ".join(codes),
                missing=", ".join(missing)))

    @staticmethod
    def _check_formula_safe(expression):
        """Reject loops/comprehensions and non-whitelisted calls before
        evaluation: keeps the formula to plain vectorised arithmetic and blocks
        CPU-exhaustion expressions such as ``sum(range(2_000_000_000))``."""
        if len(expression) > 1000:
            raise UserError(_(
                "The custom formula is too long (max 1000 characters)."))
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as error:
            raise UserError(_("Could not parse the custom formula:\n%s", error))
        allowed = set(CUSTOM_HELPERS)
        for node in ast.walk(tree):
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp,
                                 ast.GeneratorExp, ast.Lambda)):
                raise UserError(_(
                    "Loops and comprehensions are not allowed in the custom "
                    "formula — use arithmetic over the variable codes (e.g. "
                    "leads * conversion_rate)."))
            if isinstance(node, ast.Call) and (
                    not isinstance(node.func, ast.Name)
                    or node.func.id not in allowed):
                raise UserError(_(
                    "Only these functions may be called in the custom formula: "
                    "%s.", ", ".join(sorted(allowed))))
            if isinstance(node, ast.BinOp):
                # Bit operations have no business meaning here, and a shift by
                # a large constant builds an arbitrarily large Python integer.
                if isinstance(node.op, (ast.LShift, ast.RShift, ast.BitOr,
                                        ast.BitAnd, ast.BitXor)):
                    raise UserError(_(
                        "Bitwise operators are not allowed in the custom "
                        "formula."))
                if isinstance(node.op, ast.Pow):
                    MonteCarloRun._check_pow_exponent(node.right)
                    # Chained constant powers ((2**1000)**1000)**1000 multiply
                    # exponents past any single-node bound — reject them too.
                    if (not any(isinstance(sub, ast.Name)
                                for sub in ast.walk(node.left))
                            and any(isinstance(sub, ast.BinOp)
                                    and isinstance(sub.op, ast.Pow)
                                    for sub in ast.walk(node.left))):
                        raise UserError(_(
                            "Chained constant powers are not allowed in the "
                            "custom formula."))

    @staticmethod
    def _check_pow_exponent(exponent):
        """Reject power expressions that would build a huge Python integer.

        An exponent that references a variable evaluates with numpy float
        semantics (overflow becomes inf — harmless). A constant-only exponent
        evaluates with unbounded Python int semantics, so e.g. ``9**9**9`` or
        ``2**(999*999*999)`` would hang the worker on a billion-digit integer.
        Constant-only exponents are therefore limited to a single plain number
        (optionally signed) no larger than 1000."""
        has_name = any(isinstance(sub, ast.Name)
                       for sub in ast.walk(exponent))
        if has_name:
            return  # float/array semantics: no big-int blow-up possible
        node = exponent
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            node = node.operand
        if (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and abs(node.value) <= 1000):
            return
        raise UserError(_(
            "A constant exponent in the custom formula must be a single "
            "number between -1000 and 1000."))

    def _eval_custom_formula(self, samples):
        self.ensure_one()
        expression = (self.model_id.formula_expression or "").strip()
        if not expression:
            raise UserError(_("The custom formula expression is empty."))
        self._check_formula_safe(expression)
        # safe_eval mutates its context, so build a fresh dict each call.
        context = dict(samples)
        context.update(CUSTOM_HELPERS)
        try:
            result = safe_eval(expression, context, mode="eval")
        except Exception as error:  # noqa: BLE001
            raise UserError(_(
                "Could not evaluate the custom formula:\n%s", error))
        return result

    def _store_results(self, outputs, samples):
        """Persist result rows according to the model's storage mode.

        Summary statistics and the charts are computed from the full in-memory
        ``outputs``, so they stay exact regardless of how many rows are kept.
        """
        self.ensure_one()
        mode = self.model_id.result_storage or "full"
        if mode == "summary":
            return  # keep no rows; stats + distribution_data still cover analysis
        total = self.iterations
        if mode == "sample":
            cap = max(1, self.model_id.result_sample_size or 2000)
            if total > cap:
                # Seed the row picker too, so a seeded run is fully reproducible
                # (which rows are kept, not only the statistics).
                rng = np.random.default_rng(self.seed or None)
                indices = np.sort(rng.choice(total, size=cap, replace=False))
            else:
                indices = np.arange(total)
        else:  # full
            indices = np.arange(total)
        codes = list(samples.keys())
        currency_id = self.currency_id.id
        company_id = self.company_id.id
        # Create in chunks, building each chunk's values lazily: materialising
        # one vals dict per iteration (up to MAX_ITERATIONS) in a single list —
        # and one ORM cache entry per new record — would exhaust the worker's
        # memory long before the iteration cap protects it.
        Result = self.env["monte.carlo.result"].sudo()
        chunk_size = 10000
        for start in range(0, len(indices), chunk_size):
            chunk = indices[start:start + chunk_size]
            # Results are read-only for plain users; created via sudo by the
            # engine.
            Result.create([{
                "run_id": self.id,
                "iteration": int(index) + 1,
                "output_value": float(outputs[index]),
                "input_snapshot": {
                    code: float(samples[code][index]) for code in codes},
                "currency_id": currency_id,
                "company_id": company_id,
            } for index in chunk])
            # Push the rows to the database and drop them from the record
            # cache before the next chunk (invalidate_all flushes first).
            self.env.invalidate_all()

    def _calculate_summary(self, outputs):
        """Return a dict of summary statistics for the output array."""
        self.ensure_one()
        threshold = self.success_threshold
        total = outputs.shape[0]
        above = float(np.count_nonzero(outputs >= threshold)) / total * 100.0
        mean = float(np.mean(outputs))
        std = float(np.std(outputs))
        se = std / (total ** 0.5) if total else 0.0
        # Tail risk, oriented by the objective: the "bad" tail is the low end
        # for upside objectives and the high end for cost/duration/inventory.
        if self.model_id.objective in BELOW_IS_GOOD_OBJECTIVES:
            var = float(np.percentile(outputs, 95))
            tail = outputs[outputs >= var]
        else:
            var = float(np.percentile(outputs, 5))
            tail = outputs[outputs <= var]
        cvar = float(tail.mean()) if tail.size else var
        # One pass for all five percentiles instead of five full sorts.
        p05, p25, p50, p75, p95 = (
            float(p) for p in np.percentile(outputs, [5, 25, 50, 75, 95]))
        return {
            "summary_min": float(np.min(outputs)),
            "summary_max": float(np.max(outputs)),
            "negative_fraction": (float(np.count_nonzero(outputs < 0)) / total
                                  * 100.0 if total else 0.0),
            "summary_mean": mean,
            "summary_median": p50,
            "summary_std": std,
            "p05": p05,
            "p25": p25,
            "p50": p50,
            "p75": p75,
            "p95": p95,
            "probability_above_threshold": above,
            "probability_below_threshold": 100.0 - above,
            "se_mean": se,
            "mean_ci_low": mean - 1.96 * se,
            "mean_ci_high": mean + 1.96 * se,
            "var_95": var,
            "cvar_95": cvar,
            "distribution_data": self._calculate_distribution(outputs),
        }

    def _calculate_sensitivity(self, samples, outputs):
        """Rank-correlate each input with the outcome (Spearman, numpy-only)."""
        self.ensure_one()
        name_by_code = {v.code: v.name for v in self.model_id.variable_ids}
        ry = self._rankdata(outputs)
        ry = ry - ry.mean()
        ry_ss = float((ry * ry).sum())
        drivers = []
        for code, arr in samples.items():
            if float(np.ptp(arr)) == 0.0 or ry_ss == 0.0:
                corr = 0.0  # a fixed input cannot drive the outcome
            else:
                rx = self._rankdata(arr)
                rx = rx - rx.mean()
                denom = (float((rx * rx).sum()) * ry_ss) ** 0.5
                corr = float((rx * ry).sum() / denom) if denom else 0.0
            drivers.append({
                "code": code,
                "name": name_by_code.get(code, code),
                "corr": corr,
            })
        drivers.sort(key=lambda d: abs(d["corr"]), reverse=True)
        return {"drivers": drivers}

    @staticmethod
    def _rankdata(values):
        """Fractional ranks (0..n-1): tied values share the mean of their
        ordinal ranks — the standard Spearman tie treatment. Without it, two
        independent discrete inputs report a spurious correlation (~+0.17 at
        n=10k) because stable sorting breaks ties by the shared iteration
        index. A no-op for continuous draws (no ties)."""
        values = np.asarray(values)
        order = np.argsort(values, kind="mergesort")
        ranks = np.empty(len(values), dtype=float)
        ranks[order] = np.arange(len(values), dtype=float)
        uniq, inverse, counts = np.unique(
            values, return_inverse=True, return_counts=True)
        if uniq.size != values.size:  # has ties: average each group's ranks
            sums = np.bincount(inverse, weights=ranks)
            ranks = (sums / counts)[inverse]
        return ranks

    # ------------------------------------------------------------------
    # Input correlation (Iman-Conover, pure numpy)
    # ------------------------------------------------------------------
    def _apply_correlations(self, samples, rng):
        """Impose the model's target rank correlations on the drawn samples.

        Implements the Iman & Conover (1982) rank-correlation method: each
        already-drawn input column is reordered (a within-column permutation,
        so its own distribution is unchanged) so that, together, the inputs
        reproduce the requested Spearman correlation matrix. Fixed/constant
        inputs cannot be correlated and are skipped; an inconsistent
        (non-positive-definite) target is repaired to the nearest valid one.
        """
        self.ensure_one()
        correlations = self.model_id.correlation_ids
        if not correlations:
            return samples
        n = self.iterations

        def varies(code):
            arr = samples.get(code)
            return arr is not None and float(np.ptp(arr)) > 0.0

        pairs, codes = [], []
        for corr in correlations:
            c1 = corr.variable1_id.code
            c2 = corr.variable2_id.code
            if c1 == c2 or not varies(c1) or not varies(c2):
                continue  # a fixed/constant input cannot be correlated
            rho = max(-1.0, min(1.0, corr.coefficient))
            pairs.append((c1, c2, rho))
            for code in (c1, c2):
                if code not in codes:
                    codes.append(code)
        k = len(codes)
        # Need at least two varying inputs and more iterations than inputs for
        # the score correlation matrix to be invertible.
        if k < 2 or n <= k:
            if pairs:
                _logger.info(
                    "Monte Carlo run %s: %s iterations is too few for %s "
                    "correlated inputs; correlations not applied (the achieved "
                    "column will show ~0).", self.id, n, k)
            return samples

        idx = {code: i for i, code in enumerate(codes)}
        target = np.eye(k)
        for c1, c2, rho in pairs:
            i, j = idx[c1], idx[c2]
            target[i, j] = target[j, i] = rho
        target = self._nearest_psd_correlation(target)

        # Score matrix: each column an independent permutation of the same set
        # of van der Waerden (normal) scores.
        scores = self._normal_scores(n)
        score_matrix = np.empty((n, k))
        for j in range(k):
            score_matrix[:, j] = rng.permutation(scores)
        try:
            corr_scores = np.corrcoef(score_matrix, rowvar=False)
            q_factor = np.linalg.cholesky(corr_scores)
            p_factor = np.linalg.cholesky(target)
            # transformed = score_matrix @ (Q^-1)^T @ P^T, so its correlation
            # matrix equals the target.
            transformed = score_matrix @ np.linalg.solve(q_factor.T, p_factor.T)
        except np.linalg.LinAlgError:
            _logger.warning(
                "Monte Carlo run %s: could not factor the correlation matrix; "
                "running with independent inputs.", self.id)
            return samples

        # Reorder each input so its rank pattern matches the transformed scores.
        for j, code in enumerate(codes):
            # Ordinal ranks via a single argsort (equivalent to the classic
            # argsort-of-argsort, one O(n log n) pass instead of two).
            target_ranks = np.empty(n, dtype=int)
            target_ranks[np.argsort(transformed[:, j], kind="mergesort")] = (
                np.arange(n))
            samples[code] = np.sort(samples[code])[target_ranks]
        return samples

    def _correlation_report(self, samples):
        """Compare each requested correlation with the one actually achieved.

        Returns ``{"pairs": [...], "note": str}`` or ``False`` when the model
        has no correlations. Computed from the final (correlated) in-memory
        samples, so it is exact even in summary-only storage mode.
        """
        self.ensure_one()
        correlations = self.model_id.correlation_ids
        if not correlations:
            return False
        pairs, skipped, drift = [], [], False
        # Tolerance scaled to Spearman sampling noise (~1/sqrt(n)): looser for a
        # short run, tighter for a long one, so the "not reproduced" note flags
        # a genuine mismatch rather than ordinary Monte Carlo variation.
        tolerance = max(0.08, 5.0 / math.sqrt(max(self.iterations, 1)))
        for corr in correlations:
            c1 = corr.variable1_id.code
            c2 = corr.variable2_id.code
            a, b = samples.get(c1), samples.get(c2)
            if (a is None or b is None
                    or float(np.ptp(a)) == 0.0 or float(np.ptp(b)) == 0.0):
                skipped.append("%s ↔ %s" % (
                    corr.variable1_id.name, corr.variable2_id.name))
                continue
            achieved = self._spearman(a, b)
            target = float(corr.coefficient)
            if abs(achieved - target) > tolerance:
                drift = True
            pairs.append({
                "v1": corr.variable1_id.name, "v2": corr.variable2_id.name,
                "target": round(target, 3), "achieved": round(achieved, 3),
            })
        if not pairs and not skipped:
            return False
        note = ""
        if skipped:
            note = _("Skipped (a fixed input cannot be correlated): %(pairs)s.",
                     pairs=", ".join(skipped))
        elif drift:
            note = _("Some correlations could not be fully reproduced — this "
                     "happens with an inconsistent set of correlations or with "
                     "discrete inputs.")
        return {"pairs": pairs, "note": note}

    def _spearman(self, a, b):
        """Spearman rank correlation between two equal-length sample arrays."""
        ra = self._rankdata(a)
        ra = ra - ra.mean()
        rb = self._rankdata(b)
        rb = rb - rb.mean()
        denom = (float((ra * ra).sum()) * float((rb * rb).sum())) ** 0.5
        return float((ra * rb).sum() / denom) if denom else 0.0

    @staticmethod
    def _nearest_psd_correlation(matrix, epsilon=1e-6):
        """Return the nearest positive-definite correlation matrix.

        Clips negative eigenvalues to a small positive floor and renormalises
        to a unit diagonal. The floor is 1e-6 (not far smaller) so the repaired
        matrix stays well-conditioned and Cholesky is numerically safe. A no-op
        for a matrix that is already valid; repairs a user-entered inconsistent
        set.
        """
        sym = (matrix + matrix.T) / 2.0
        vals, vecs = np.linalg.eigh(sym)
        if float(vals.min()) >= epsilon:
            return sym
        vals = np.clip(vals, epsilon, None)
        repaired = (vecs * vals) @ vecs.T
        scale = np.sqrt(np.diag(repaired))
        repaired = repaired / np.outer(scale, scale)
        np.fill_diagonal(repaired, 1.0)
        return repaired

    @staticmethod
    def _normal_scores(n):
        """Van der Waerden scores: normal quantiles of evenly spaced ranks."""
        i = np.arange(1, n + 1, dtype=float)
        return MonteCarloRun._normal_ppf(i / (n + 1.0))

    @staticmethod
    def _normal_ppf(p):
        """Vectorised inverse normal CDF (Acklam's approximation).

        Pure numpy (scipy is not a dependency); relative error < 1.2e-9, which
        is far finer than needed for the correlation scores.
        """
        p = np.asarray(p, dtype=float)
        a = (-3.969683028665376e+01, 2.209460984245205e+02,
             -2.759285104469687e+02, 1.383577518672690e+02,
             -3.066479806614716e+01, 2.506628277459239e+00)
        b = (-5.447609879822406e+01, 1.615858368580409e+02,
             -1.556989798598866e+02, 6.680131188771972e+01,
             -1.328068155288572e+01)
        c = (-7.784894002430293e-03, -3.223964580411365e-01,
             -2.400758277161838e+00, -2.549732539343734e+00,
             4.374664141464968e+00, 2.938163982698783e+00)
        d = (7.784695709041462e-03, 3.224671290700398e-01,
             2.445134137142996e+00, 3.754408661907416e+00)
        plow, phigh = 0.02425, 1.0 - 0.02425
        x = np.zeros_like(p)
        lo = p < plow
        if np.any(lo):
            q = np.sqrt(-2.0 * np.log(p[lo]))
            x[lo] = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4])
                     * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3])
                                    * q + 1.0)
        hi = p > phigh
        if np.any(hi):
            q = np.sqrt(-2.0 * np.log(1.0 - p[hi]))
            x[hi] = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4])
                      * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3])
                                     * q + 1.0)
        mid = ~(lo | hi)
        if np.any(mid):
            q = p[mid] - 0.5
            r = q * q
            x[mid] = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4])
                      * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r
                                           + b[3]) * r + b[4]) * r + 1.0)
        return x

    def _render_correlation_html(self, data):
        """Compact table: requested vs achieved input correlation."""
        self.ensure_one()
        pairs = (data or {}).get("pairs") or []
        if not pairs:
            return False
        head = ('<tr style="background:#14385f;color:#fff;">'
                '<th style="padding:4px 8px;text-align:start;">%s</th>'
                '<th style="padding:4px 8px;text-align:end;">%s</th>'
                '<th style="padding:4px 8px;text-align:end;">%s</th></tr>'
                % (_("Inputs"), _("Target"), _("Achieved")))
        rows = []
        for pair in pairs:
            rows.append(
                '<tr>'
                '<td style="padding:4px 8px;border-top:1px solid #e3e8ee;">'
                '%s ↔ %s</td>'
                '<td style="padding:4px 8px;border-top:1px solid #e3e8ee;'
                'text-align:end;">%+.2f</td>'
                '<td style="padding:4px 8px;border-top:1px solid #e3e8ee;'
                'text-align:end;">%+.2f</td></tr>'
                % (self._svg_escape(pair["v1"]), self._svg_escape(pair["v2"]),
                   pair["target"], pair["achieved"]))
        note = (data or {}).get("note")
        note_html = ('<div style="margin-top:4px;font-size:12px;color:#5b6b7b;">'
                     '%s</div>' % self._svg_escape(note)) if note else ""
        return ('<div style="max-width:520px;"><table style="border-collapse:'
                'collapse;font-size:13px;width:100%%;">%s%s</table>%s</div>'
                % (head, "".join(rows), note_html))

    def _calculate_distribution(self, outputs):
        """Bin the outputs into a compact histogram + cumulative curve."""
        self.ensure_one()
        total = int(outputs.shape[0]) or 1
        lo = float(np.min(outputs))
        hi = float(np.max(outputs))
        bins = None
        if hi > lo:
            try:
                counts, edges = np.histogram(
                    outputs, bins=DISTRIBUTION_BINS, range=(lo, hi))
                cumulative = np.cumsum(counts)
                bins = [{
                    "x0": float(edges[i]),
                    "x1": float(edges[i + 1]),
                    "count": int(counts[i]),
                    "cdf": float(cumulative[i]) / total,
                } for i in range(len(counts))]
            except ValueError:
                # Range too small to subdivide at this magnitude (e.g. a near-
                # constant value around 1e15): fall back to a single bin.
                bins = None
        if bins is None:
            # Constant / unbinnable output: one bin holds every scenario.
            bins = [{"x0": lo, "x1": hi, "count": int(outputs.shape[0]),
                     "cdf": 1.0}]
        p05, p50, p95 = (
            float(p) for p in np.percentile(outputs, [5, 50, 95]))
        return {
            "bins": bins,
            "n": int(outputs.shape[0]),
            "min": lo,
            "max": hi,
            "mean": float(np.mean(outputs)),
            "p05": p05,
            "p50": p50,
            "p95": p95,
            "threshold": float(self.success_threshold),
        }

    # ------------------------------------------------------------------
    # PDF report helpers (wkhtmltopdf-safe CSS bars, no SVG)
    # ------------------------------------------------------------------
    def _report_amount(self, value):
        self.ensure_one()
        return self._format_amount(value or 0.0)

    def _report_subtitle(self):
        self.ensure_one()
        return _("%s scenarios") % "{:,}".format(
            self.result_count or self.iterations)

    def _report_success_label(self):
        """Orientation-aware caption for the success probability, so the PDF
        matches the on-screen banner for 'lower is better' objectives."""
        self.ensure_one()
        if self._success_metric()[1]:
            return _("Chance of staying within target")
        return _("Chance of reaching target")

    def _report_distribution_bars(self):
        """Return histogram bars (height in px) for the QWeb report."""
        self.ensure_one()
        bins = (self.distribution_data or {}).get("bins") or []
        max_count = max((b["count"] for b in bins), default=1) or 1
        width = max(4, int(620 / (len(bins) or 1)))
        return [{"h": round(b["count"] / max_count * 130.0, 1),
                 "w": width, "count": b["count"]} for b in bins]

    def _report_drivers(self):
        """Return the top drivers with bar widths/colours for the report."""
        self.ensure_one()
        drivers = (self.sensitivity_data or {}).get("drivers") or []
        out = []
        for driver in drivers[:8]:
            corr = max(-1.0, min(1.0, driver["corr"]))
            out.append({
                "name": driver["name"], "corr": corr,
                "pct": round(abs(corr) * 100.0, 1),
                "color": "#2b6cb0" if corr >= 0 else "#e0884b",
            })
        return out

    @staticmethod
    def _fmt_compact(value):
        """Short axis label, e.g. 2.1M / 480K / 73."""
        a = abs(value)
        if a >= 1e9:
            return "%.1fB" % (value / 1e9)
        if a >= 1e6:
            return "%.1fM" % (value / 1e6)
        if a >= 1e3:
            return "%.0fK" % (value / 1e3)
        if a >= 10:
            return "%.0f" % value
        return "%.1f" % value

    def _render_distribution_svg(self, data):
        """Build a self-contained SVG (histogram + CDF S-curve + markers).

        Rendered server-side as inline SVG so it needs no JavaScript asset and
        also shows up in printed/PDF reports later.
        """
        self.ensure_one()
        bins = (data or {}).get("bins") or []
        if not bins:
            return False
        width, height = 1000.0, 460.0
        ml, mr, mt, mb = 12.0, 40.0, 34.0, 56.0
        plot_w = width - ml - mr
        plot_h = height - mt - mb
        base_y = mt + plot_h
        xmin, xmax = data["min"], data["max"]
        span0 = (xmax - xmin) or 1.0
        thr = data.get("threshold", 0.0)
        thr_in = (xmin - 0.15 * span0) <= thr <= (xmax + 0.15 * span0)
        lo = min(xmin, thr) if thr_in else xmin
        hi = max(xmax, thr) if thr_in else xmax
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo

        def sx(value):
            return ml + (value - lo) / span * plot_w

        max_count = max((b["count"] for b in bins), default=1) or 1
        parts = []
        # 50% reference gridline
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#e3e8ee" stroke-width="1"/>'
                     % (ml, base_y - 0.5 * plot_h, ml + plot_w, base_y - 0.5 * plot_h))
        # histogram bars
        for b in bins:
            x = sx(b["x0"])
            w = max(sx(b["x1"]) - x - 1.0, 0.5)
            bh = b["count"] / max_count * plot_h
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                         'fill="#7ba7d7" opacity="0.85"/>'
                         % (x, base_y - bh, w, bh))
        # cumulative S-curve (CDF), 0..100% mapped to plot height
        pts = ["%.1f,%.1f" % (sx(bins[0]["x0"]), base_y)]
        for b in bins:
            pts.append("%.1f,%.1f" % (sx(b["x1"]), base_y - b["cdf"] * plot_h))
        parts.append('<polyline points="%s" fill="none" stroke="#14385f" '
                     'stroke-width="2.5"/>' % " ".join(pts))

        def vline(value, color, dash, stroke=1.6):
            x = sx(min(max(value, lo), hi))
            extra = ' stroke-dasharray="5,4"' if dash else ''
            return ('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                    'stroke-width="%.1f"%s/>'
                    % (x, mt, x, base_y, color, stroke, extra))

        for key in ("p05", "p50", "p95"):
            parts.append(vline(data[key], "#9aa7b4", True))
        parts.append(vline(data["mean"], "#2b6cb0", False, 2.0))
        parts.append('<text x="%.1f" y="%.1f" fill="#2b6cb0" font-size="13" '
                     'text-anchor="middle">μ</text>'
                     % (sx(min(max(data["mean"], lo), hi)), mt - 6))
        if thr_in:
            parts.append(vline(thr, "#e05b49", True, 1.8))
            parts.append('<text x="%.1f" y="%.1f" fill="#e05b49" font-size="12" '
                         'text-anchor="middle">%s</text>'
                         % (sx(thr), base_y + 36, _("Threshold")))
        # x-axis baseline + value ticks
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#9aa7b4" stroke-width="1"/>'
                     % (ml, base_y, ml + plot_w, base_y))
        for value, anchor in ((lo, "start"), ((lo + hi) / 2.0, "middle"),
                              (hi, "end")):
            parts.append('<text x="%.1f" y="%.1f" fill="#5b6b7b" font-size="12" '
                         'text-anchor="%s">%s</text>'
                         % (sx(value), base_y + 18, anchor, self._fmt_compact(value)))
        # right-side cumulative scale
        for frac, label in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
            parts.append('<text x="%.1f" y="%.1f" fill="#14385f" font-size="11" '
                         'text-anchor="start">%s</text>'
                         % (ml + plot_w + 4, base_y - frac * plot_h + 4, label))

        svg = ('<svg viewBox="0 0 1000 460" preserveAspectRatio="xMidYMid meet" '
               'style="width:100%%;height:auto;font-family:sans-serif;">%s</svg>'
               % "".join(parts))

        def chip(color, label):
            return ('<span style="margin-inline-end:14px;white-space:nowrap;">'
                    '<span style="display:inline-block;width:11px;height:11px;'
                    'background:%s;border-radius:2px;vertical-align:middle;'
                    'margin-inline-end:4px;"></span>%s</span>' % (color, label))

        legend = (
            '<div style="margin-top:4px;font-size:12px;color:#5b6b7b;">'
            + chip("#7ba7d7", _("Frequency"))
            + chip("#14385f", _("Cumulative probability"))
            + chip("#2b6cb0", _("Mean"))
            + chip("#9aa7b4", _("Percentiles (P5/P50/P95)"))
            + (chip("#e05b49", _("Threshold")) if thr_in else "")
            + '</div>')
        return ('<div style="width:100%%;max-width:920px;">%s%s</div>'
                % (svg, legend))

    def _render_sensitivity_svg(self, data):
        """Build a tornado chart (signed rank-correlation bars) as inline SVG."""
        self.ensure_one()
        drivers = [d for d in ((data or {}).get("drivers") or [])][:8]
        if not drivers:
            return False
        row_h = 30.0
        top = 28.0
        label_w = 230.0
        plot_left = label_w + 16.0
        plot_right = 980.0
        cx = (plot_left + plot_right) / 2.0
        half = (plot_right - plot_left) / 2.0
        height = top + len(drivers) * row_h + 30.0
        parts = []
        # axis scale ticks -1 .. +1
        for frac in (-1.0, -0.5, 0.0, 0.5, 1.0):
            x = cx + frac * half
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="%s" stroke-width="1"/>'
                         % (x, top - 6, x, top + len(drivers) * row_h,
                            "#9aa7b4" if frac == 0.0 else "#eceff3"))
            parts.append('<text x="%.1f" y="%.1f" fill="#8795a4" font-size="11" '
                         'text-anchor="middle">%s</text>'
                         % (x, top + len(drivers) * row_h + 16,
                            ("%+.1f" % frac) if frac else "0"))
        for i, d in enumerate(drivers):
            y = top + i * row_h
            corr = max(-1.0, min(1.0, d["corr"]))
            x_end = cx + corr * half
            x0 = min(cx, x_end)
            w = max(abs(x_end - cx), 0.5)
            color = "#2b6cb0" if corr >= 0 else "#e0884b"
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                         'rx="2" fill="%s" opacity="0.9"/>'
                         % (x0, y + 4, w, row_h - 12, color))
            parts.append('<text x="%.1f" y="%.1f" fill="#3b4754" font-size="13" '
                         'text-anchor="end">%s</text>'
                         % (label_w, y + row_h / 2.0 + 1, self._svg_escape(d["name"])))
            anchor = "start" if corr >= 0 else "end"
            dx = 5 if corr >= 0 else -5
            parts.append('<text x="%.1f" y="%.1f" fill="#5b6b7b" font-size="11" '
                         'text-anchor="%s">%+.2f</text>'
                         % (x_end + dx, y + row_h / 2.0 + 1, anchor, corr))
        svg = ('<svg viewBox="0 0 1000 %d" preserveAspectRatio="xMidYMid meet" '
               'style="width:100%%;height:auto;font-family:sans-serif;">%s</svg>'
               % (int(height), "".join(parts)))
        caption = ('<div style="margin-top:2px;font-size:12px;color:#5b6b7b;">%s</div>'
                   % _("Bar length = how strongly each input drives the outcome "
                       "(rank correlation). Blue pushes the outcome up, orange "
                       "pushes it down."))
        return ('<div style="width:100%%;max-width:920px;">%s%s</div>'
                % (svg, caption))

    @staticmethod
    def _svg_escape(text):
        return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;")
                .replace("'", "&#39;"))

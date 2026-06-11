# -*- coding: utf-8 -*-
import math

from odoo import api, fields, models, _

# Distinct, colour-blind-friendly line colours for the overlaid CDF curves.
CMP_COLORS = ["#2b6cb0", "#e05b49", "#38a169", "#d69e2e", "#805ad5",
              "#dd6b20", "#319795", "#b83280"]


class MonteCarloComparison(models.TransientModel):
    _name = "monte.carlo.comparison"
    _description = "Monte Carlo Scenario Comparison"

    run_ids = fields.Many2many(
        "monte.carlo.run", string="Runs to Compare",
        domain="[('state', '=', 'done')]",
        help="Pick two or more completed runs to compare side by side.")
    note = fields.Char(compute="_compute_comparison")
    comparison_chart = fields.Html(
        string="Cumulative Probability", compute="_compute_comparison",
        sanitize=False,
        help="Each curve is one run's cumulative probability (CDF): for any "
             "amount on the X axis, the height is the chance of ending at or "
             "below it. A curve further to the right means higher outcomes — "
             "which is better for revenue/profit goals, but worse for "
             "cost/time goals (there, further left is better).")
    comparison_table = fields.Html(
        string="Side-by-side Figures", compute="_compute_comparison",
        sanitize=False)

    # ------------------------------------------------------------------
    # Seed the selection from the runs ticked in the list view
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Only consume active_ids when they really are runs: the wizard could
        # be opened from another model's context (e.g. a server action).
        if self.env.context.get("active_model") not in (None, "monte.carlo.run"):
            return res
        active_ids = self.env.context.get("active_ids") or []
        if active_ids and "run_ids" in fields_list:
            runs = self.env["monte.carlo.run"].browse(active_ids).filtered(
                lambda r: r.state == "done")
            res["run_ids"] = [(6, 0, runs.ids)]
        return res

    @staticmethod
    def _orientation(runs):
        """Return (mixed, below_good). ``below_good`` is True when *lower* is the
        better outcome (cost / duration / inventory objectives); ``mixed`` is
        True when the selected runs do not share one orientation, in which case
        a single 'winner' direction is meaningless."""
        orientations = {run._success_metric()[1] for run in runs}
        if len(orientations) > 1:
            return True, False
        return False, orientations.pop()

    @api.depends("run_ids")
    def _compute_comparison(self):
        for rec in self:
            runs = rec.run_ids.filtered(lambda r: r.state == "done")
            if len(runs) < 2:
                rec.note = _("Select at least two completed runs to compare.")
                rec.comparison_chart = False
                rec.comparison_table = False
                continue
            notes = []
            if len(runs.mapped("currency_id")) > 1:
                notes.append(_(
                    "The selected runs use different currencies (%s); compare "
                    "the shapes, not the absolute amounts.")
                    % ", ".join(runs.mapped("currency_id.name")))
            if rec._orientation(runs)[0]:
                notes.append(_(
                    "The selected runs mix 'higher is better' and 'lower is "
                    "better' objectives, so the best-run highlight is turned "
                    "off — read the figures directly."))
            rec.note = " ".join(notes) or False
            rec.comparison_chart = rec._render_comparison_svg(runs)
            rec.comparison_table = rec._build_comparison_table(runs)

    # ------------------------------------------------------------------
    # Side-by-side metrics table (rows = metric, columns = run)
    # ------------------------------------------------------------------
    def _build_comparison_table(self, runs):
        self.ensure_one()
        Run = self.env["monte.carlo.run"]
        esc = Run._svg_escape
        mixed, below_good = self._orientation(runs)
        # Direction of "better" for value metrics: lower for cost/time/inventory
        # objectives, higher otherwise; None (no winner) when objectives differ.
        val_dir = None if mixed else ("low" if below_good else "high")
        success = {r.id: r._success_metric()[0] for r in runs}

        def amt(run, value):
            if value is None or not math.isfinite(value):
                return "—"
            return esc(run._format_amount(value))

        # (label, getter, values_or_None, direction) — direction 'high'/'low'/
        # None marks (green) the best run for that metric. Value metrics flip
        # with the objective; spread / success / negatives have a fixed sense.
        rows = [
            (_("Model"), lambda r: esc(r.model_id.name), None, None),
            (_("Iterations"), lambda r: "{:,}".format(r.iterations), None, None),
            (_("Mean (expected)"), lambda r: amt(r, r.summary_mean),
             [r.summary_mean for r in runs], val_dir),
            (_("Median (P50)"), lambda r: amt(r, r.p50),
             [r.p50 for r in runs], val_dir),
            (_("Downside (P5)"), lambda r: amt(r, r.p05),
             [r.p05 for r in runs], val_dir),
            (_("Upside (P95)"), lambda r: amt(r, r.p95),
             [r.p95 for r in runs], val_dir),
            (_("Std. deviation"), lambda r: amt(r, r.summary_std),
             [r.summary_std for r in runs], "low"),
            (_("Worst-5% mean (CVaR)"), lambda r: amt(r, r.cvar_95),
             [r.cvar_95 for r in runs], val_dir),
            (_("Success confidence"),
             lambda r: "%.1f%%" % success[r.id],
             [success[r.id] for r in runs], "high"),
            (_("% negative"),
             lambda r: "%.1f%%" % r.negative_fraction,
             [r.negative_fraction for r in runs], "low"),
        ]

        th = ('padding:6px 10px;border-bottom:2px solid #cbd5e0;'
              'text-align:start;font-weight:600;white-space:nowrap;')
        td = 'padding:6px 10px;border-bottom:1px solid #edf1f5;white-space:nowrap;'
        head = ['<th style="%s">%s</th>' % (th, _("Metric"))]
        for idx, run in enumerate(runs):
            color = CMP_COLORS[idx % len(CMP_COLORS)]
            head.append(
                '<th style="%stext-align:end;border-bottom-color:%s;">'
                '<span style="display:inline-block;width:10px;height:10px;'
                'background:%s;border-radius:2px;margin-inline-end:5px;"></span>'
                '%s</th>' % (th, color, color, esc(run.name)))

        body = []
        for label, getter, values, direction in rows:
            cells = ['<td style="%scolor:#5b6b7b;">%s</td>' % (td, label)]
            best_idx = None
            if direction and values is not None:
                # ignore non-finite values; only flag a winner if runs differ
                finite = [(i, v) for i, v in enumerate(values)
                          if v is not None and math.isfinite(v)]
                if finite and len({v for _i, v in finite}) > 1:
                    target = (max if direction == "high" else min)(
                        v for _i, v in finite)
                    best_idx = next(i for i, v in finite if v == target)
            for idx, run in enumerate(runs):
                strong = (idx == best_idx)
                style = (td + "text-align:end;font-variant-numeric:tabular-nums;"
                         + ("background:#e9f7ef;font-weight:600;color:#1e7e44;"
                            if strong else ""))
                cells.append('<td style="%s">%s</td>' % (style, getter(run)))
            body.append("<tr>%s</tr>" % "".join(cells))

        caption = (_("Objectives differ, so no best-run is highlighted.")
                   if mixed else (
                       _("Green marks the best run for each metric "
                         "(lower is better for this objective).") if below_good
                       else _("Green marks the best run for each metric.")))
        return (
            '<table style="border-collapse:collapse;width:100%%;font-size:13px;">'
            '<thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
            '<div style="margin-top:6px;font-size:12px;color:#8b97a3;">%s</div>'
            % ("".join(head), "".join(body), caption))

    # ------------------------------------------------------------------
    # Overlaid cumulative-probability (CDF) curves — one polyline per run
    # ------------------------------------------------------------------
    def _render_comparison_svg(self, runs):
        self.ensure_one()
        Run = self.env["monte.carlo.run"]
        data = [(r, (r.distribution_data or {})) for r in runs]
        data = [(r, d) for r, d in data if d.get("bins")]
        if len(data) < 2:
            return False

        width, height = 1000.0, 460.0
        ml, mr, mt, mb = 14.0, 44.0, 20.0, 56.0
        plot_w, plot_h = width - ml - mr, height - mt - mb
        base_y = mt + plot_h
        lo = min(d["min"] for _r, d in data)
        hi = max(d["max"] for _r, d in data)
        if hi <= lo:
            hi = lo + 1.0
        span = hi - lo

        def sx(v):
            return ml + (min(max(v, lo), hi) - lo) / span * plot_w

        parts = []
        # horizontal reference gridlines at 0/25/50/75/100%
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = base_y - frac * plot_h
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                         'stroke="#eef2f6" stroke-width="1"/>'
                         % (ml, y, ml + plot_w, y))
        # one CDF polyline per run
        for idx, (run, d) in enumerate(data):
            color = CMP_COLORS[idx % len(CMP_COLORS)]
            bins = d["bins"]
            pts = ["%.1f,%.1f" % (sx(bins[0]["x0"]), base_y)]
            for b in bins:
                pts.append("%.1f,%.1f" % (sx(b["x1"]),
                                          base_y - b["cdf"] * plot_h))
            parts.append('<polyline points="%s" fill="none" stroke="%s" '
                         'stroke-width="2.5" opacity="0.9"/>'
                         % (" ".join(pts), color))
            # median marker (dot where the curve crosses 50%)
            parts.append('<circle cx="%.1f" cy="%.1f" r="3.5" fill="%s"/>'
                         % (sx(d["p50"]), base_y - 0.5 * plot_h, color))

        # axes
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#9aa7b4" stroke-width="1"/>'
                     % (ml, base_y, ml + plot_w, base_y))
        for v, anchor in ((lo, "start"), ((lo + hi) / 2.0, "middle"),
                          (hi, "end")):
            parts.append('<text x="%.1f" y="%.1f" fill="#5b6b7b" font-size="12" '
                         'text-anchor="%s">%s</text>'
                         % (sx(v), base_y + 18, anchor, Run._fmt_compact(v)))
        for frac, label in ((0.0, "0%"), (0.5, "50%"), (1.0, "100%")):
            parts.append('<text x="%.1f" y="%.1f" fill="#14385f" font-size="11">'
                         '%s</text>'
                         % (ml + plot_w + 4, base_y - frac * plot_h + 4, label))

        svg = ('<svg viewBox="0 0 1000 460" preserveAspectRatio="xMidYMid meet" '
               'style="width:100%%;height:auto;font-family:sans-serif;">%s</svg>'
               % "".join(parts))

        chips = []
        for idx, (run, _d) in enumerate(data):
            color = CMP_COLORS[idx % len(CMP_COLORS)]
            chips.append(
                '<span style="margin-inline-end:14px;white-space:nowrap;">'
                '<span style="display:inline-block;width:11px;height:11px;'
                'background:%s;border-radius:2px;vertical-align:middle;'
                'margin-inline-end:4px;"></span>%s</span>'
                % (color, Run._svg_escape(run.name)))
        mixed, below_good = self._orientation(runs)
        if mixed:
            hint = _("dot = median; objectives differ — read each curve on its "
                     "own terms")
        elif below_good:
            hint = _("dot = median; a curve further left (lower) is better")
        else:
            hint = _("dot = median; a curve further right (higher) is better")
        legend = ('<div style="margin-top:4px;font-size:12px;color:#5b6b7b;">'
                  '%s<span style="margin-inline-start:6px;">• %s</span></div>'
                  % ("".join(chips), hint))
        return svg + legend

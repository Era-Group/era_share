# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _


class CrmLead(models.Model):
    """Add a Monte Carlo "reality check" to an opportunity, beside Odoo's own
    win-probability prediction: by resampling the company's CONFIRMED sale orders
    (by product, by similar size, dated by confirmation date) it answers "what
    share of our comparable deals actually reach this value?" — a figure that
    varies with the quote, unlike a fixed band."""
    _name = "crm.lead"
    _inherit = ["crm.lead", "monte.carlo.simulable"]

    mc_run_id = fields.Many2one(
        "monte.carlo.run", string="Value Simulation", copy=False,
        readonly=True, help="The latest Monte Carlo value simulation.")
    mc_basis_data = fields.Json(
        string="Forecast Basis", copy=False, readonly=True,
        help="Snapshot of which comparable-order basis the last simulation "
             "used (product / similar / all, sample size, period). Stored so "
             "displaying the forecast never re-mines the sale orders.")
    mc_forecast = fields.Html(
        string="Monte Carlo Forecast", compute="_compute_mc_forecast",
        sanitize=False,
        help="Share of the company's comparable confirmed orders that reach "
             "this value, plus the typical value and range — from a Monte Carlo "
             "resampling of confirmed sale orders (separate from Odoo's win "
             "prediction).")

    def _mc_growth_factor(self):
        """Natural-growth uplift applied to past deal values to project them to
        today. Tunable via the ``era_monte_carlo_simulation.mc_growth_pct``
        system parameter (percent); defaults to 7%."""
        pct = self.env["ir.config_parameter"].sudo().get_param(
            "era_monte_carlo_simulation.mc_growth_pct", "7")
        try:
            return max(0.0, float(pct)) / 100.0
        except (TypeError, ValueError):
            return 0.07

    def _mc_comparable_values(self):
        """Pick the most relevant CONFIRMED sale orders to compare this deal
        against (deal sizes vary hugely by product, so 'all deals' is
        meaningless). Always uses confirmed orders (real values) dated by their
        confirmation/order date, preferring the last 12 months. Smart fallback:

          1. PRODUCT  — confirmed orders containing this opportunity's products,
          2. SIMILAR  — confirmed orders within ±50% of this deal's value,
          3. ALL      — every confirmed order (flagged as broad).

        Returns ``(values, basis, count, recent)`` — basis in product/similar/
        all, ``recent`` True when the last-12-months window was used.
        """
        self.ensure_one()
        company = self.company_id or self.env.company
        currency = self.company_currency or company.currency_id
        quote = self.expected_revenue or 0.0
        # Untaxed amounts: expected_revenue is untaxed by convention, so taxed
        # totals would bias the comparison upward by the VAT rate. (sale.order
        # has no 'done' state since Odoo 17 — confirmed means state='sale'.)
        base = [("state", "=", "sale"), ("amount_untaxed", ">", 0),
                ("company_id", "in", [False, company.id])]
        cutoff = fields.Datetime.to_string(
            fields.Datetime.now() - relativedelta(months=12))
        SO = self.env["sale.order"]
        # Project past values to today with an assumed natural-growth rate.
        scale = 1.0 + self._mc_growth_factor()

        def convert(orders):
            # Express every order in the lead's currency: face values from
            # different currencies cannot be compared or resampled together.
            vals = []
            for order in orders:
                value = order.currency_id._convert(
                    order.amount_untaxed, currency,
                    order.company_id or company,
                    order.date_order or fields.Date.context_today(self))
                if value > 0:
                    vals.append(value * scale)
            return vals

        def fetch(extra):
            # Prefer confirmed orders from the last 12 months (by date_order);
            # fall back to all-time for this basis when too few.
            recent = SO.search(base + extra + [("date_order", ">=", cutoff)],
                               limit=500)
            vals = convert(recent)
            if len(vals) >= 5:
                return vals, True
            allt = SO.search(base + extra, limit=500)
            return convert(allt), False

        products = self.order_ids.order_line.product_id
        if products:
            vals, recent = fetch([("order_line.product_id", "in", products.ids),
                                  ("id", "not in", self.order_ids.ids)])
            if len(vals) >= 5:
                return vals, "product", len(vals), recent
        if quote > 0:
            # The size band is an SQL filter, so it can only compare amounts
            # in one currency: restrict this basis to same-currency orders.
            vals, recent = fetch([("currency_id", "=", currency.id),
                                  ("amount_untaxed", ">=", quote * 0.5),
                                  ("amount_untaxed", "<=", quote * 1.5)])
            if len(vals) >= 5:
                return vals, "similar", len(vals), recent
        vals, recent = fetch([])
        return vals, "all", len(vals), recent

    def _mc_recipe(self):
        self.ensure_one()
        quote = self.expected_revenue or 0.0
        company = self.company_id or self.env.company
        currency = self.company_currency or company.currency_id
        values, basis, count, recent = self._mc_comparable_values()
        # Snapshot the basis for the forecast note: _compute_mc_forecast must
        # only format stored data, never re-mine the sale orders on render.
        self.mc_basis_data = {"basis": basis, "n": count, "recent": recent}
        if len(values) >= 5:
            # Bootstrap: resample the actual comparable deal sizes, so
            # P(value ≥ quote) is the real share of comparable deals reaching it.
            variable = {
                "name": _("Comparable deal value"), "code": "average_deal_value",
                "distribution": "discrete",
                "discrete_values": ", ".join("%.2f" % v for v in values),
            }
        else:
            # No usable history: fall back to ±30% around the quote.
            variable = self._mc_band(_("Deal value"), "average_deal_value",
                                     quote, lo=0.7, hi=1.3)
        return {
            "name": _("Deal value — %s", self.name or _("Opportunity")),
            "objective": "revenue_forecast",
            "formula_type": "custom_python_limited",
            "formula_expression": "average_deal_value",
            "company_id": company.id,
            "currency_id": currency.id,
            "success_threshold": round(quote, 2),
            # Keep only the summary stats — the forecast never needs the bulky
            # per-iteration result rows.
            "result_storage": "summary",
            "variables": [variable],
        }

    @staticmethod
    def _mc_box(label, value, color="#14385f"):
        esc = lambda t: (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
                         .replace(">", "&gt;"))
        return ('<div style="border:1px solid #d3dce6;border-radius:6px;'
                'padding:3px 12px;background:#fff;text-align:center;">'
                '<div style="font-size:11px;color:#8b97a3;white-space:nowrap;">'
                '%s</div><div style="font-size:14px;font-weight:600;color:%s;'
                'white-space:nowrap;">%s</div></div>'
                % (esc(label), color, esc(value)))

    @api.depends("mc_run_id", "mc_run_id.state", "mc_run_id.summary_mean",
                 "mc_run_id.p25", "mc_run_id.p50", "mc_run_id.p75",
                 "mc_run_id.probability_above_threshold", "mc_basis_data")
    def _compute_mc_forecast(self):
        for lead in self:
            run = lead.mc_run_id
            if not (run and run.state == "done"):
                lead.mc_forecast = (
                    '<span style="color:#8b97a3;font-style:italic;">%s</span>'
                    % _("Not computed yet — click Recompute."))
                continue
            fmt = run._format_amount
            # Read the stored basis snapshot — re-mining the comparable orders
            # here would run several sale.order searches on every form render.
            data = lead.mc_basis_data or {}
            basis, n = data.get("basis"), data.get("n", 0)
            recent = data.get("recent")
            if not data:
                # Run from before the snapshot existed: infer the branch from
                # the run itself (discrete variable = history-based forecast).
                history = run.model_id.variable_ids.filtered(
                    lambda v: v.distribution == "discrete")
                n = 5 if history else 0
            boxes = []
            if n >= 5:
                pct = run.probability_above_threshold
                color = ("#1e7e44" if pct >= 50 else
                         "#b54708" if pct >= 20 else "#b42318")
                boxes.append(lead._mc_box(
                    _("Chance of reaching this value"), "%.0f%%" % pct, color))
                # Median, not mean: deal sizes are right-skewed, so the mean is
                # inflated by a few big deals and is NOT the typical deal.
                boxes.append(lead._mc_box(
                    _("Typical comparable deal"), fmt(run.p50)))
                # Middle-50% (interquartile) range: the "usual" deals, excluding
                # the extreme small/large outliers that distort P5–P95.
                boxes.append(lead._mc_box(
                    _("Usual range (middle 50%)"),
                    "%s – %s" % (fmt(run.p25), fmt(run.p75))))
                basis_text = {
                    "product": _("%s confirmed orders with these products", n),
                    "similar": _("%s confirmed orders of similar size (±50%%)", n),
                    "all": _("%s confirmed orders (add products for a sharper "
                             "estimate)", n),
                }.get(basis, _("click Recompute to refresh the details"))
                period = ("" if recent is None
                          else _("last 12 months") if recent else _("all time"))
                parts = [basis_text, period]
                growth = lead._mc_growth_factor()
                if growth:
                    parts.append(_("+%(pct).0f%% projected growth",
                                   pct=growth * 100))
                # Full-width row of its own, free to wrap onto several lines:
                # flex-basis:100% drops it below the boxes, white-space:normal
                # lets a long basis ("similar size (±50%) · …") wrap instead of
                # overflowing the card.
                note = ('<span style="font-size:11px;color:#8b97a3;'
                        'flex-basis:100%;width:100%;white-space:normal;">'
                        '%s</span>' % " · ".join(p for p in parts if p))
            else:
                boxes.append(lead._mc_box(
                    _("Expected value"), fmt(run.summary_mean)))
                boxes.append(lead._mc_box(
                    _("Likely range"),
                    "%s – %s" % (fmt(run.p05), fmt(run.p95))))
                note = ('<span style="font-size:11px;color:#8b97a3;'
                        'flex-basis:100%;width:100%;white-space:normal;">'
                        '%s</span>' % _("(±30% — not enough deal history)"))
            lead.mc_forecast = (
                '<div style="display:flex;gap:8px;flex-wrap:wrap;'
                'align-items:center;">%s%s</div>' % ("".join(boxes), note))

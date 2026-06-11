# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError


class MonteCarloSimulable(models.AbstractModel):
    """Mixin that turns any business record into a one-click Monte Carlo
    simulation. A model inherits it and implements ``_mc_recipe`` to describe
    the simulation (objective, formula and uncertain variables) from its own
    fields; the button then builds/refreshes a linked simulation model, runs it
    and opens the result.
    """
    _name = "monte.carlo.simulable"
    _description = "Monte Carlo Simulable Mixin"

    def _mc_recipe(self):
        """Return a dict describing the simulation for this record:

            {"name", "objective", "formula_type", "currency_id"(optional),
             "formula_expression"(optional),
             "variables": [ {distribution + params}, ... ]}

        Must be overridden by the inheriting model.
        """
        raise UserError(_("This record type cannot be simulated yet."))

    @staticmethod
    def _mc_band(name, code, center, lo=0.7, hi=1.3, cap=None, floor=0.0):
        """Build a Triangular(center*lo, center, center*hi) variable to express
        ``±`` uncertainty around a known central value, with guards: clamps to
        an optional ceiling and falls back to Fixed when the value is
        non-positive or the band collapses (so it always validates)."""
        center = float(center or 0.0)
        if cap is not None:
            center = min(cap, center)
        mn = max(floor, center * lo)
        mx = center * hi
        if cap is not None:
            mx = min(cap, mx)
        if center <= floor or not (mn < center < mx):
            return {"name": name, "code": code,
                    "distribution": "fixed", "fixed_value": center}
        return {"name": name, "code": code, "distribution": "triangular",
                "min_value": mn, "mode_value": center, "max_value": mx}

    def action_monte_carlo_simulate(self):
        """Build/refresh the linked simulation model from this record and run it.

        The run executes during this call (the button shows its native progress
        spinner meanwhile); we then stay on the source record and refresh it so
        the result appears in place, instead of navigating away to the run.
        """
        self.ensure_one()
        recipe = self._mc_recipe()
        model = self.env["monte.carlo.model"]._mc_sync_from_recipe(self, recipe)
        run = model._mc_simulate_now(
            success_threshold=recipe.get("success_threshold"))
        if "mc_run_id" in self._fields:
            self.mc_run_id = run.id
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

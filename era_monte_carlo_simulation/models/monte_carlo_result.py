# -*- coding: utf-8 -*-
from odoo import api, fields, models


class MonteCarloResult(models.Model):
    _name = "monte.carlo.result"
    _description = "Monte Carlo Simulation Result"
    _order = "run_id, iteration"
    # One row per simulated scenario. Rows are written in batch by the engine
    # and are read-only in the UI.

    run_id = fields.Many2one(
        "monte.carlo.run", string="Run",
        required=True, ondelete="cascade", index=True,
        help="The run that produced this result row.")
    model_id = fields.Many2one(
        related="run_id.model_id", string="Model", store=True, index=True,
        help="The simulation model behind this result.")
    iteration = fields.Integer(
        string="Iteration", index=True,
        help="Scenario number within the run (1 to the number of iterations).")
    output_value = fields.Float(
        string="Output Value",
        help="The simulated output for this single scenario.")
    input_snapshot = fields.Json(
        string="Input Snapshot",
        help="The exact input values used for this scenario, keyed by variable "
             "code.")
    input_snapshot_display = fields.Char(
        string="Inputs", compute="_compute_input_snapshot_display",
        help="Human-readable view of the inputs used for this scenario.")

    currency_id = fields.Many2one(
        "res.currency", string="Currency",
        help="Currency of the output value.")
    company_id = fields.Many2one(
        "res.company", string="Company",
        default=lambda self: self.env.company,
        help="Company that owns this result.")

    @api.depends("input_snapshot")
    def _compute_input_snapshot_display(self):
        for record in self:
            snapshot = record.input_snapshot or {}
            record.input_snapshot_display = ", ".join(
                "%s=%s" % (key, round(value, 4) if isinstance(value, float)
                           else value)
                for key, value in snapshot.items())

# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class MonteCarloCorrelation(models.Model):
    """A target rank correlation between two input variables of a model.

    Independent random draws assume every input moves on its own. In reality
    inputs often move together (a bigger pipeline tends to have smaller average
    deals; a higher price dampens demand). Declaring a correlation here makes
    the engine reorder the drawn samples so the inputs vary together by the
    requested amount — without changing any single input's own distribution
    (see ``monte.carlo.run._apply_correlations``, Iman-Conover method).
    """
    _name = "monte.carlo.correlation"
    _description = "Monte Carlo Input Correlation"
    _order = "model_id, id"

    model_id = fields.Many2one(
        "monte.carlo.model", string="Simulation Model",
        required=True, ondelete="cascade", index=True,
        help="The simulation model this correlation belongs to.")
    variable1_id = fields.Many2one(
        "monte.carlo.variable", string="First Variable",
        required=True, ondelete="cascade",
        help="One of the two inputs that move together.")
    variable2_id = fields.Many2one(
        "monte.carlo.variable", string="Second Variable",
        required=True, ondelete="cascade",
        help="The other input. Must belong to the same model and differ from "
             "the first variable.")
    coefficient = fields.Float(
        string="Correlation", required=True, default=0.5,
        help="Target rank (Spearman) correlation between -1 and 1. "
             "+1 = move together perfectly, 0 = independent, "
             "-1 = move in opposite directions. Typical real-world links are "
             "moderate (e.g. -0.6 between price and demand). Fixed inputs "
             "cannot be correlated and are ignored.")
    company_id = fields.Many2one(
        related="model_id.company_id", string="Company", store=True,
        help="Company that owns this correlation (taken from the model).")

    @api.depends("variable1_id.name", "variable2_id.name", "coefficient")
    def _compute_display_name(self):
        for record in self:
            v1 = record.variable1_id.name or "?"
            v2 = record.variable2_id.name or "?"
            record.display_name = "%s ↔ %s (%+.2f)" % (
                v1, v2, record.coefficient)

    @api.constrains("coefficient")
    def _check_coefficient(self):
        for record in self:
            if not (-1.0 <= record.coefficient <= 1.0):
                raise ValidationError(_(
                    "A correlation must be between -1 and 1 (got %s).",
                    record.coefficient))

    @api.constrains("variable1_id", "variable2_id", "model_id")
    def _check_variables(self):
        for record in self:
            if record.variable1_id == record.variable2_id:
                raise ValidationError(_(
                    "A correlation must link two DIFFERENT variables."))
            for variable in (record.variable1_id, record.variable2_id):
                if variable.model_id != record.model_id:
                    raise ValidationError(_(
                        "Variable '%(var)s' does not belong to model "
                        "'%(model)s'.",
                        var=variable.name, model=record.model_id.name))
            # An unordered pair must be unique within a model: (A,B) == (B,A).
            duplicate = self.search_count([
                ("id", "!=", record.id),
                ("model_id", "=", record.model_id.id),
                ("variable1_id", "in",
                 (record.variable1_id.id, record.variable2_id.id)),
                ("variable2_id", "in",
                 (record.variable1_id.id, record.variable2_id.id)),
            ])
            if duplicate:
                raise ValidationError(_(
                    "There is already a correlation between '%(a)s' and "
                    "'%(b)s' in this model.",
                    a=record.variable1_id.name, b=record.variable2_id.name))

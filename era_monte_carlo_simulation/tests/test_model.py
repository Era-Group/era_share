# -*- coding: utf-8 -*-
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMonteCarloModel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Model = cls.env["monte.carlo.model"]
        cls.model = cls.Model.create({
            "name": "Copy Test", "objective": "profit_analysis",
            "formula_type": "custom_python_limited",
            "formula_expression": "units * unit_price",
        })
        cls.v1, cls.v2 = cls.env["monte.carlo.variable"].create([
            {"model_id": cls.model.id, "name": "Units", "code": "units",
             "distribution": "normal", "mean_value": 100, "std_dev": 10},
            {"model_id": cls.model.id, "name": "Price", "code": "unit_price",
             "distribution": "uniform", "min_value": 5, "max_value": 15},
        ])
        cls.env["monte.carlo.correlation"].create({
            "model_id": cls.model.id, "variable1_id": cls.v1.id,
            "variable2_id": cls.v2.id, "coefficient": -0.4,
        })

    def test_copy_clones_variables_and_correlations(self):
        clone = self.model.copy()
        self.assertEqual(len(clone.variable_ids), 2)
        self.assertEqual(
            set(clone.variable_ids.mapped("code")), {"units", "unit_price"})
        self.assertEqual(len(clone.correlation_ids), 1)
        corr = clone.correlation_ids
        # the correlation points at the CLONED variables, not the originals
        self.assertIn(corr.variable1_id, clone.variable_ids)
        self.assertIn(corr.variable2_id, clone.variable_ids)
        self.assertEqual(corr.coefficient, -0.4)
        # and the copy is immediately runnable
        run = self.env["monte.carlo.run"].create(
            {"model_id": clone.id, "iterations": 500, "seed": 3})
        run.with_context(mc_skip_ai_summary=True).action_run_simulation()
        self.assertEqual(run.state, "done")

    def test_latest_run_snapshot(self):
        self.assertEqual(self.model.latest_risk, "none")
        run = self.env["monte.carlo.run"].create({
            "model_id": self.model.id, "iterations": 1000, "seed": 9,
            "success_threshold": 0})
        run.with_context(mc_skip_ai_summary=True).action_run_simulation()
        self.assertEqual(self.model.latest_run_id, run)
        self.assertEqual(self.model.latest_mean, run.summary_mean)
        self.assertIn(self.model.latest_risk, ("high", "medium", "low"))
        self.assertEqual(self.model.latest_driver,
                         (run.sensitivity_data["drivers"][0]["name"]))

    def test_recipe_sync_rebuilds_cleanly(self):
        recipe = {
            "name": "Recipe Model", "objective": "revenue_forecast",
            "formula_type": "custom_python_limited",
            "formula_expression": "a * b",
            "variables": [
                {"name": "A", "code": "a", "distribution": "fixed",
                 "fixed_value": 2.0},
                {"name": "B", "code": "b", "distribution": "uniform",
                 "min_value": 1.0, "max_value": 3.0},
            ],
            "correlations": [],
        }
        source = self.env["res.partner"].create({"name": "Recipe Source"})
        model = self.Model._mc_sync_from_recipe(source, recipe)
        self.assertEqual(len(model.variable_ids), 2)
        # re-sync reuses the SAME model and rebuilds the variables
        model2 = self.Model._mc_sync_from_recipe(source, recipe)
        self.assertEqual(model, model2)
        self.assertEqual(len(model2.variable_ids), 2)

    def test_user_cannot_unlink_models_or_runs(self):
        user = self.env["res.users"].create({
            "name": "MC User", "login": "mc_user_test",
            "group_ids": [(4, self.env.ref(
                "era_monte_carlo_simulation.group_monte_carlo_user").id)],
        })
        model = self.Model.with_user(user).create({
            "name": "User Model", "objective": "custom",
            "formula_type": "custom_python_limited",
            "formula_expression": "x",
        })
        with self.assertRaises(AccessError):
            model.with_user(user).unlink()

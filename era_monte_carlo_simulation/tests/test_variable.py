# -*- coding: utf-8 -*-
import numpy as np

from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestMonteCarloVariable(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Variable = cls.env["monte.carlo.variable"]
        cls.model = cls.env["monte.carlo.model"].create({
            "name": "Variable Test", "objective": "custom",
            "formula_type": "custom_python_limited",
            "formula_expression": "x",
        })

    def _variable(self, **vals):
        return self.Variable.create(dict(
            {"model_id": self.model.id, "name": "X", "code": "x",
             "distribution": "fixed", "fixed_value": 1.0}, **vals))

    def test_sampling_respects_distributions(self):
        rng = np.random.default_rng(5)
        tri = self._variable(code="tri", distribution="triangular",
                             min_value=10, mode_value=20, max_value=30)
        samples = tri.generate_samples(5000, random_state=rng)
        self.assertGreaterEqual(float(samples.min()), 10.0)
        self.assertLessEqual(float(samples.max()), 30.0)

        logn = self._variable(code="logn", distribution="lognormal",
                              mean_value=50000, std_dev=12000)
        samples = logn.generate_samples(20000, random_state=rng)
        self.assertGreater(float(samples.min()), 0.0)  # strictly positive
        # method of moments: sample mean/std match the requested ones
        self.assertLess(abs(samples.mean() - 50000) / 50000, 0.05)
        self.assertLess(abs(samples.std() - 12000) / 12000, 0.10)

        disc = self._variable(code="disc", distribution="discrete",
                              discrete_values="10, 20, 30")
        samples = disc.generate_samples(1000, random_state=rng)
        self.assertEqual(set(np.unique(samples)), {10.0, 20.0, 30.0})

    def test_parameter_validation(self):
        with self.assertRaises(ValidationError):  # min >= max
            self._variable(code="bad1", distribution="uniform",
                           min_value=10, max_value=10)
        with self.assertRaises(ValidationError):  # mode outside range
            self._variable(code="bad2", distribution="triangular",
                           min_value=10, mode_value=40, max_value=30)
        with self.assertRaises(ValidationError):  # negative std
            self._variable(code="bad3", distribution="normal",
                           mean_value=10, std_dev=0)
        with self.assertRaises(ValidationError):  # lognormal needs mean > 0
            self._variable(code="bad4", distribution="lognormal",
                           mean_value=0, std_dev=5)
        with self.assertRaises(ValidationError):  # bad code
            self._variable(code="9bad")
        with self.assertRaises(ValidationError):  # inf in discrete values
            self._variable(code="bad5", distribution="discrete",
                           discrete_values="1, inf")

    def test_duplicate_code_blocked(self):
        self._variable(code="dup")
        # The SQL constraint fires at flush (before the python one can):
        # uniqueness is guaranteed at the database level.
        with self.assertRaises(IntegrityError), \
                mute_logger("odoo.sql_db"), self.cr.savepoint():
            self._variable(code="dup")
            self.env.flush_all()

    def test_duplicate_code_blocked_on_reparenting(self):
        other = self.env["monte.carlo.model"].create({
            "name": "Other", "objective": "custom",
            "formula_type": "custom_python_limited",
            "formula_expression": "y",
        })
        self._variable(code="y")
        moved = self.Variable.create({
            "model_id": other.id, "name": "Y", "code": "y",
            "distribution": "fixed", "fixed_value": 1.0})
        with self.assertRaises(IntegrityError), \
                mute_logger("odoo.sql_db"), self.cr.savepoint():
            moved.model_id = self.model  # same code lands in target model
            self.env.flush_all()

    def test_fit_distribution(self):
        var = self._variable(code="fit", distribution="normal",
                             mean_value=1, std_dev=1)
        # plain positive data with wide spread auto-upgrades to lognormal
        values = np.array([10.0, 12.0, 15.0, 100.0, 11.0, 13.0])
        vals = var._fit_distribution(values)
        self.assertEqual(vals.get("distribution"), "lognormal")
        # constant data collapses to fixed instead of inventing a spread
        vals = var._fit_distribution(np.array([5.0, 5.0, 5.0]))
        self.assertEqual(vals.get("distribution"), "fixed")
        self.assertEqual(vals.get("fixed_value"), 5.0)
        # triangular fits P5/P50/P95, not raw min/max
        var_tri = self._variable(code="fit_tri", distribution="triangular",
                                 min_value=0, mode_value=1, max_value=2)
        data = np.concatenate([np.linspace(10, 20, 99), [1000.0]])
        vals = var_tri._fit_distribution(data)
        self.assertLess(vals["max_value"], 1000.0)  # outlier did not blow up

    def test_ai_tool_statistics(self):
        result = self.Variable._ai_tool_statistics(
            "res.partner", field_name="id", domain="[]")
        self.assertGreater(result["n"], 0)
        self.assertIn("std", result)
        # malformed non-empty domain is an error, not a silent match-all
        result = self.Variable._ai_tool_statistics(
            "res.partner", field_name="id", domain="not a domain")
        self.assertIn("error", result)
        # ratio mode without a numerator filter is an error
        result = self.Variable._ai_tool_statistics(
            "res.partner", mode="ratio", date_field="create_date")
        self.assertIn("error", result)

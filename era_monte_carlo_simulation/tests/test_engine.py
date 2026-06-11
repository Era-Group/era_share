# -*- coding: utf-8 -*-
import numpy as np

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMonteCarloEngine(TransactionCase):
    """End-to-end engine behaviour: runs, statistics, storage modes,
    correlations and the custom-formula sandbox."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Model = cls.env["monte.carlo.model"]
        cls.Run = cls.env["monte.carlo.run"]
        cls.model = cls.Model.create({
            "name": "Engine Test Revenue",
            "objective": "revenue_forecast",
            "formula_type": "simple_revenue",
            "default_iterations": 2000,
        })
        cls.env["monte.carlo.variable"].create([
            {"model_id": cls.model.id, "name": "Leads", "code": "leads",
             "distribution": "triangular", "min_value": 100,
             "mode_value": 180, "max_value": 300},
            {"model_id": cls.model.id, "name": "Conversion",
             "code": "conversion_rate", "distribution": "uniform",
             "min_value": 0.10, "max_value": 0.35},
            {"model_id": cls.model.id, "name": "Deal Value",
             "code": "average_deal_value", "distribution": "lognormal",
             "mean_value": 50000, "std_dev": 12000},
        ])

    def _run(self, **vals):
        run = self.Run.create(dict(
            {"model_id": self.model.id, "seed": 42,
             "success_threshold": 2000000}, **vals))
        run.with_context(mc_skip_ai_summary=True).action_run_simulation()
        return run

    def test_run_produces_consistent_statistics(self):
        run = self._run()
        self.assertEqual(run.state, "done")
        self.assertGreater(run.summary_mean, 0)
        self.assertLessEqual(run.summary_min, run.p05)
        self.assertLessEqual(run.p05, run.p25)
        self.assertLessEqual(run.p25, run.p50)
        self.assertLessEqual(run.p50, run.p75)
        self.assertLessEqual(run.p75, run.p95)
        self.assertLessEqual(run.p95, run.summary_max)
        self.assertAlmostEqual(run.summary_median, run.p50, places=6)
        self.assertAlmostEqual(
            run.probability_above_threshold
            + run.probability_below_threshold, 100.0, places=6)
        # 95% CI is symmetric around the mean
        self.assertAlmostEqual(
            run.summary_mean - run.mean_ci_low,
            run.mean_ci_high - run.summary_mean, places=4)
        self.assertEqual(run.result_count, 2000)
        self.assertTrue((run.distribution_data or {}).get("bins"))
        drivers = (run.sensitivity_data or {}).get("drivers") or []
        self.assertEqual(len(drivers), 3)

    def test_seeded_runs_reproducible(self):
        run1 = self._run()
        run2 = self._run()
        self.assertEqual(run1.summary_mean, run2.summary_mean)
        self.assertEqual(run1.p95, run2.p95)
        self.assertEqual(
            run1.result_ids[0].output_value, run2.result_ids[0].output_value)

    def test_storage_modes(self):
        self.model.result_storage = "summary"
        run = self._run()
        self.assertEqual(len(run.result_ids), 0)
        self.assertGreater(run.summary_mean, 0)  # stats stay exact

        self.model.write({"result_storage": "sample",
                          "result_sample_size": 500})
        run_sample = self._run()
        self.assertEqual(len(run_sample.result_ids), 500)
        # summary mode and sample mode agree on the (seeded) statistics
        self.assertEqual(run.summary_mean, run_sample.summary_mean)
        self.model.result_storage = "full"

    def test_failed_run_keeps_no_rows_and_clears(self):
        run = self._run()
        self.assertEqual(run.state, "done")
        # Sabotage: drop a required variable and re-run -> must fail cleanly.
        self.model.variable_ids.filtered(
            lambda v: v.code == "leads").unlink()
        run.action_run_simulation()
        self.assertEqual(run.state, "failed")
        self.assertTrue(run.error_message)
        self.assertEqual(len(run.result_ids), 0)
        self.assertEqual(run.summary_mean, 0.0)
        self.assertEqual(run.negative_fraction, 0.0)

    def test_negative_seed_rejected(self):
        with self.assertRaises(ValidationError):
            self.Run.create({"model_id": self.model.id, "seed": -1})

    def test_below_is_good_orientation(self):
        cost_model = self.Model.create({
            "name": "Engine Test Cost", "objective": "cost_estimation",
            "formula_type": "custom_python_limited",
            "formula_expression": "spend",
        })
        self.env["monte.carlo.variable"].create({
            "model_id": cost_model.id, "name": "Spend", "code": "spend",
            "distribution": "normal", "mean_value": 100, "std_dev": 10,
        })
        run = self.Run.create({"model_id": cost_model.id, "seed": 7,
                               "iterations": 2000,
                               "success_threshold": 100})
        run.with_context(mc_skip_ai_summary=True).action_run_simulation()
        prob, below_good = run._success_metric()
        self.assertTrue(below_good)
        self.assertEqual(prob, run.probability_below_threshold)
        # the bad tail for a cost objective is the HIGH end
        self.assertGreater(run.var_95, run.p50)
        self.assertGreaterEqual(run.cvar_95, run.var_95)


@tagged("post_install", "-at_install")
class TestFormulaSafety(TransactionCase):
    """The custom-formula AST guard must block resource-exhaustion shapes."""

    def check(self, expression):
        # (kept as an instance helper: storing the staticmethod on the class
        # would rebind it as a method and shift the arguments)
        return self.env["monte.carlo.run"]._check_formula_safe(expression)

    def test_valid_formulas_pass(self):
        for expr in ("leads * conversion_rate * average_deal_value",
                     "max(units, 0) * (unit_price - unit_cost)",
                     "where(x > 0, x, 0) + sqrt(abs(y))",
                     "x ** 2 + 10**6 * y",
                     "x ** -0.5"):
            self.check(expr)  # must not raise

    def test_blocked_formulas(self):
        cases = (
            "sum(range(2000000000))",         # non-whitelisted call
            "[x for x in range(10)]",          # comprehension
            "(lambda: 1)()",                   # lambda
            "9**9**9**9",                      # nested constant powers
            "2**999999",                       # huge constant exponent
            "2**(999*999*999)",                # constant arithmetic exponent
            "((2**1000)**1000)**1000",         # chained constant powers
            "1 << 1000000000",                 # bitwise shift
            "x" * 1001,                        # over the length cap
        )
        for expr in cases:
            with self.assertRaises(UserError, msg=expr):
                self.check(expr)


@tagged("post_install", "-at_install")
class TestRankStatistics(TransactionCase):
    """Tie-aware Spearman ranks (discrete inputs must not show spurious
    correlation) and the Iman-Conover correlation engine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Run = cls.env["monte.carlo.run"]

    def test_tied_ranks_are_averaged(self):
        ranks = self.Run._rankdata(np.array([10.0, 20.0, 10.0, 30.0]))
        # the two 10s share rank (0+1)/2 = 0.5
        self.assertEqual(list(ranks), [0.5, 2.0, 0.5, 3.0])

    def test_independent_discrete_inputs_uncorrelated(self):
        rng = np.random.default_rng(123)
        a = rng.choice([1.0, 2.0, 3.0], 10000)
        b = rng.choice([10.0, 20.0, 30.0], 10000)
        self.assertLess(abs(self.Run._spearman(a, b)), 0.05)

    def test_iman_conover_hits_target_and_keeps_marginals(self):
        model = self.env["monte.carlo.model"].create({
            "name": "Corr Test", "objective": "profit_analysis",
            "formula_type": "custom_python_limited",
            "formula_expression": "units * unit_price",
        })
        v1, v2 = self.env["monte.carlo.variable"].create([
            {"model_id": model.id, "name": "Units", "code": "units",
             "distribution": "normal", "mean_value": 1000, "std_dev": 100},
            {"model_id": model.id, "name": "Price", "code": "unit_price",
             "distribution": "uniform", "min_value": 50, "max_value": 150},
        ])
        self.env["monte.carlo.correlation"].create({
            "model_id": model.id, "variable1_id": v1.id,
            "variable2_id": v2.id, "coefficient": -0.6,
        })
        run = self.Run.create({"model_id": model.id, "seed": 11,
                               "iterations": 5000})
        samples = run._generate_samples()
        achieved = self.Run._spearman(
            samples["units"], samples["unit_price"])
        self.assertLess(abs(achieved - (-0.6)), 0.05)
        # marginals untouched: reordering must not change the sorted values
        rng = np.random.default_rng(11)
        independent = v1.generate_samples(5000, random_state=rng)
        self.assertTrue(np.array_equal(
            np.sort(samples["units"]), np.sort(independent)))

    def test_nearest_psd_repairs_inconsistent_matrix(self):
        bad = np.array([[1.0, 0.9, -0.9],
                        [0.9, 1.0, 0.9],
                        [-0.9, 0.9, 1.0]])  # impossible correlation set
        repaired = self.Run._nearest_psd_correlation(bad)
        eigenvalues = np.linalg.eigvalsh(repaired)
        self.assertGreater(float(eigenvalues.min()), 0)
        self.assertTrue(np.allclose(np.diag(repaired), 1.0))
        np.linalg.cholesky(repaired)  # must not raise

    def test_normal_ppf_accuracy(self):
        # Acklam's approximation vs known quantiles
        p = np.array([0.025, 0.5, 0.975])
        expected = np.array([-1.959964, 0.0, 1.959964])
        self.assertTrue(np.allclose(
            self.Run._normal_ppf(p), expected, atol=1e-4))

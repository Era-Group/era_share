# -*- coding: utf-8 -*-
"""How the builder decides who answers: the matcher, the planner, or nobody.

Every branch here costs the user something different — a second of waiting, a
model call somebody pays for, or a queue entry instead of an answer — so which
one runs when is worth pinning down.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase


class TestWhoAnswers(TransactionCase):

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]
        self.planner = self.env["tour.assistant.planner"]
        self.menu = self.env.ref("base.menu_administration")

    def test_an_outright_match_never_asks_the_model(self):
        """Spending a call to confirm the obvious makes every clear question slow."""
        asked = []
        with patch.object(type(self.builder), "_best_menu",
                          return_value=(self.menu, 1.0)), \
                patch.object(type(self.planner), "plan_task",
                             side_effect=lambda *a: asked.append(a) or ([], "")):
            stages, reason = self.builder._plan("الإعدادات")
        self.assertEqual(asked, [])
        self.assertEqual([stage["menu"] for stage in stages], [self.menu])
        self.assertEqual(reason, "")

    def test_a_partial_match_is_evidence_not_a_decision(self):
        """طلب اجازة and طلبات الصيانة share one word and nothing else."""
        with patch.object(type(self.builder), "_best_menu",
                          return_value=(self.menu, 0.5)), \
                patch.object(type(self.planner), "plan_task",
                             return_value=([], "")), \
                patch.object(type(self.planner), "available", return_value=True):
            stages, reason = self.builder._plan("طلب اجازة")
        self.assertEqual(stages, [])

    def test_a_partial_match_survives_when_nothing_can_be_asked(self):
        """Without a planner it is all there is, and it was enough before."""
        with patch.object(type(self.builder), "_best_menu",
                          return_value=(self.menu, 0.5)), \
                patch.object(type(self.planner), "plan_task",
                             return_value=([], "")), \
                patch.object(type(self.planner), "available", return_value=False):
            stages, dummy = self.builder._plan("طلب اجازة")
        self.assertEqual([stage["menu"] for stage in stages], [self.menu])

    def test_the_planner_overrules_a_partial_match(self):
        other = self.env.ref("base.menu_users")
        planned = [{"menu": other, "goal": "g", "create": False, "fields": []}]
        with patch.object(type(self.builder), "_best_menu",
                          return_value=(self.menu, 0.5)), \
                patch.object(type(self.planner), "plan_task",
                             return_value=(planned, "")):
            stages, dummy = self.builder._plan("سؤال")
        self.assertEqual([stage["menu"] for stage in stages], [other])

    def test_a_refusal_carries_its_reason_out(self):
        excuse = "تطبيق الحضور غير مثبت."
        with patch.object(type(self.builder), "_best_menu",
                          return_value=(self.env["ir.ui.menu"], 0.0)), \
                patch.object(type(self.planner), "plan_task",
                             return_value=([], excuse)):
            stages, reason = self.builder._plan("البصمة")
        self.assertEqual(stages, [])
        self.assertEqual(reason, excuse)


class TestBuildingFromAPlan(TransactionCase):

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _stage(self, xmlid):
        return {"menu": self.env.ref(xmlid), "goal": "", "create": False,
                "fields": []}

    def _build(self, stages):
        with patch.object(type(self.builder), "_plan",
                          return_value=(stages, "")):
            return self.builder.build_with_reason("سؤال اختبار %d" % len(stages))

    def test_every_stage_reaches_the_tour(self):
        stages = [self._stage("base.menu_administration"),
                  self._stage("base.menu_users")]
        tour, dummy = self._build(stages)
        self.addCleanup(tour.sudo().unlink)
        self.assertEqual(
            set(tour.assistant_menu_ids.ids),
            {stage["menu"].id for stage in stages},
        )

    def test_a_stage_that_cannot_be_pointed_at_does_not_sink_the_rest(self):
        nameless = self.env["ir.ui.menu"].create({"name": "Nameless"})
        stages = [self._stage("base.menu_administration"),
                  {"menu": nameless, "goal": "", "create": False, "fields": []}]
        tour, dummy = self._build(stages)
        self.addCleanup(tour.sudo().unlink)
        self.assertEqual(tour.assistant_menu_ids.ids,
                         [self.env.ref("base.menu_administration").id])

    def test_a_plan_of_nothing_but_unpointable_stages_builds_nothing(self):
        nameless = self.env["ir.ui.menu"].create({"name": "Nameless"})
        tour, reason = self._build(
            [{"menu": nameless, "goal": "", "create": False, "fields": []}]
        )
        self.assertFalse(tour)
        self.assertEqual(reason, "")

    def test_a_walkthrough_is_cut_before_it_stops_being_one(self):
        """A plan longer than a person will follow is still cut off."""
        from odoo.addons.era_web_tour_assistant.models import tour_builder
        plan = [self._stage(xmlid) for xmlid in (
            "base.menu_administration", "base.menu_users",
            "base.menu_action_res_users", "base.menu_administration",
            "base.menu_users",
        )]
        tour, dummy = self._build(plan)
        self.addCleanup(tour.sudo().unlink)
        self.assertLessEqual(len(tour.step_ids), tour_builder.MAX_STEPS)

    def test_the_cut_falls_between_stages_and_not_inside_one(self):
        """Ending between New and Save leaves a half filled form and no pointer."""
        from odoo.addons.era_web_tour_assistant.models import tour_builder
        stage = self._stage("base.menu_administration")
        one = len(self.builder._stage_steps(stage, opening=True))
        self.assertTrue(one)
        # More stages than the cap can hold, all the same length.
        plan = [dict(stage) for dummy in range(tour_builder.MAX_STEPS)]
        tour, ignored = self._build(plan)
        self.addCleanup(tour.sudo().unlink)
        # Whole stages only: the first is one length, the rest another, since
        # a later stage leaves its app first.
        later = len(self.builder._stage_steps(stage, opening=False))
        remainder = (len(tour.step_ids) - one) % later
        self.assertEqual(remainder, 0)

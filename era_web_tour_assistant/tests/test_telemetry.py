# -*- coding: utf-8 -*-
"""The promise that a customer's words never leave their database.

A comment saying "no text is sent" is worth nothing the day somebody adds a
field to the snapshot in a hurry. These tests are what makes it a promise: a
question containing a distinctive string must not appear anywhere in the
payload, at any depth, however it was serialised.
"""

import json

from odoo.tests.common import TransactionCase


class TestNothingAPersonWroteIsSent(TransactionCase):

    SECRET = "AlRajhiInvoice77channel"

    def setUp(self):
        super().setUp()
        self.telemetry = self.env["tour.assistant.telemetry"]
        self.requests = self.env["tour.assistant.request"].sudo()

    def _ask(self, state="queued", **values):
        return self.requests.create(dict({
            "name": "كيف اسوي فاتورة لـ %s" % self.SECRET,
            "question_key": self.SECRET,
            "state": state,
            "ask_count": 3,
            "build_error": "تطبيق %s غير مثبت" % self.SECRET,
            "match_detail": self.SECRET,
        }, **values))

    def test_the_question_is_not_in_the_payload(self):
        self._ask()
        payload = json.dumps(self.telemetry._snapshot(), ensure_ascii=False)
        self.assertNotIn(self.SECRET, payload)

    def test_neither_is_the_reason_it_could_not_be_answered(self):
        """build_error is written by a model and can quote the question."""
        self._ask()
        payload = json.dumps(self.telemetry._snapshot(), ensure_ascii=False)
        self.assertNotIn("غير مثبت", payload)

    def test_nor_the_words_that_matched(self):
        self._ask()
        payload = json.dumps(self.telemetry._snapshot(), ensure_ascii=False)
        self.assertNotIn("match_detail", payload)

    def test_a_reported_walkthrough_travels_as_menus_and_numbers(self):
        tour = self.env["web_tour.tour"].sudo().create({
            "name": "telemetry_probe", "custom": True,
            "assistant_generated": True,
            "assistant_menu_ids": [(6, 0, [self.env.ref("base.menu_administration").id])],
        })
        self._ask(state="matched", tour_id=tour.id, reported_count=2)
        snapshot = self.telemetry._snapshot()
        self.assertTrue(snapshot["reported"], "a report has to reach us somehow")
        row = snapshot["reported"][0]
        self.assertIn("base.menu_administration", row["menus"])
        self.assertEqual(row["reports"], 2)
        self.assertNotIn(self.SECRET, json.dumps(row, ensure_ascii=False))

    def test_the_counts_are_actually_counted(self):
        """Sending nothing useful would be its own kind of failure."""
        self._ask()
        snapshot = self.telemetry._snapshot()
        self.assertGreaterEqual(snapshot["questions"]["asked"], 3)
        self.assertGreaterEqual(snapshot["questions"]["queued"], 1)


class TestItStaysOffUntilAsked(TransactionCase):

    def setUp(self):
        super().setUp()
        self.telemetry = self.env["tour.assistant.telemetry"]
        self.setting = self.env["ir.config_parameter"].sudo()

    def test_off_by_default(self):
        self.assertFalse(self.telemetry._enabled())
        self.assertFalse(self.telemetry.send())

    def test_a_switch_without_an_address_sends_nothing(self):
        self.setting.set_param("era_web_tour_assistant.telemetry", "True")
        self.assertFalse(self.telemetry._enabled())

    def test_both_together_are_what_turns_it_on(self):
        self.setting.set_param("era_web_tour_assistant.telemetry", "True")
        self.setting.set_param(
            "era_web_tour_assistant.telemetry_url", "https://example.invalid/report")
        self.assertTrue(self.telemetry._enabled())

    def test_an_unreachable_endpoint_is_not_an_error_here(self):
        """A walkthrough must not stop working because reporting moved."""
        self.setting.set_param("era_web_tour_assistant.telemetry", "True")
        self.setting.set_param(
            "era_web_tour_assistant.telemetry_url", "http://127.0.0.1:9/nowhere")
        self.assertFalse(self.telemetry.send())

# -*- coding: utf-8 -*-
"""Send-window + cultural-norms engine tests (pure logic, no provider, no DB
writes). Times are built from Riyadh wall-clock and converted to the naive UTC
the engine expects.
"""
from datetime import datetime

import pytz

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_compliance.services.send_window import SendWindow
from odoo.addons.era_crm_ai_agents_compliance.services.norms import check_norms

_TZ = "Asia/Riyadh"


def _riyadh_utc(y, mo, d, h, mi):
    local = pytz.timezone(_TZ).localize(datetime(y, mo, d, h, mi))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


@tagged("post_install", "-at_install")
class TestSendWindow(TransactionCase):

    def setUp(self):
        super().setUp()
        self.sw = SendWindow()

    def test_prayer_time_blocked(self):
        # 12:05 Riyadh falls inside the Dhuhr block.
        ok, reason = self.sw.is_send_allowed(_riyadh_utc(2026, 6, 10, 12, 5), _TZ)
        self.assertFalse(ok)
        self.assertIn("prayer", reason)

    def test_business_hours_allowed(self):
        ok, _reason = self.sw.is_send_allowed(_riyadh_utc(2026, 6, 10, 10, 0), _TZ)
        self.assertTrue(ok)

    def test_after_hours_blocked(self):
        ok, reason = self.sw.is_send_allowed(_riyadh_utc(2026, 6, 10, 23, 0), _TZ)
        self.assertFalse(ok)
        self.assertIn("business hours", reason)

    def test_next_allowed_slot_is_concrete_and_allowed(self):
        now = _riyadh_utc(2026, 6, 10, 12, 5)  # inside Dhuhr
        slot = self.sw.next_allowed_slot(now, _TZ)
        self.assertGreater(slot, now)
        self.assertTrue(self.sw.is_send_allowed(slot, _TZ)[0])

    def test_next_slot_after_hours_rolls_to_next_morning(self):
        slot = self.sw.next_allowed_slot(_riyadh_utc(2026, 6, 10, 23, 0), _TZ)
        self.assertTrue(self.sw.is_send_allowed(slot, _TZ)[0])


@tagged("post_install", "-at_install")
class TestCulturalNorms(TransactionCase):

    def test_good_message_passes(self):
        ok, issues = check_norms("السلام عليكم أستاذ خالد، تحية طيبة.")
        self.assertTrue(ok)
        self.assertFalse(issues)

    def test_missing_greeting_flagged(self):
        ok, issues = check_norms("أستاذ خالد نود المتابعة.")
        self.assertFalse(ok)
        self.assertIn("missing greeting", issues)

    def test_missing_honorific_flagged(self):
        ok, issues = check_norms("السلام عليكم، نود المتابعة.")
        self.assertFalse(ok)
        self.assertIn("missing honorific", issues)

    def test_empty_message_flagged(self):
        ok, issues = check_norms("   ")
        self.assertFalse(ok)
        self.assertIn("empty message", issues)

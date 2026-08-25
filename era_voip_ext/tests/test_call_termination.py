from datetime import datetime, timedelta

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "era_voip_ext")
class TestCallTermination(TransactionCase):
    def setUp(self):
        super().setUp()
        self.started_at = datetime(2026, 8, 25, 9, 38, 43)
        self.call = self.env["voip.call"].create({
            "phone_number": "+966500000001",
            "state": "ongoing",
            "start_date": self.started_at,
        })

    def test_first_termination_time_is_authoritative(self):
        first_end = self.started_at + timedelta(seconds=2)
        late_end = self.started_at + timedelta(seconds=89)

        self.assertTrue(self.call._finalize_if_ongoing(ended_at=first_end))
        self.assertFalse(self.call._finalize_if_ongoing(ended_at=late_end))

        self.assertEqual(self.call.state, "terminated")
        self.assertEqual(self.call.end_date, first_end)

    def test_late_end_call_can_fill_activity_without_moving_end_time(self):
        first_end = self.started_at + timedelta(seconds=2)
        self.call._finalize_if_ongoing(ended_at=first_end)

        self.call.end_call(activity_name="Follow up")

        self.assertEqual(self.call.end_date, first_end)
        self.assertEqual(self.call.activity_name, "Follow up")

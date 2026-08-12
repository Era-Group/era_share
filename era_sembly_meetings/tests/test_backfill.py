# -*- coding: utf-8 -*-
"""The historical backfill.

Importing a whole history cannot happen in a web request: one get_meeting round
trip per meeting runs for minutes, and the HTTP worker is killed at
limit_time_real (240s on this instance) with nothing saved. So the wizard only
arms a cron, and these tests pin the three properties that makes it depend on —
it resumes, it never silently skips a window, and it stops on its own.
"""
from datetime import date, timedelta
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyBackfill(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.icp = self.env['ir.config_parameter'].sudo()
        self.cron = self.env.ref('era_sembly_meetings.cron_sembly_backfill')
        self.icp.set_param('sembly.mcp_token', 'tok-for-tests')

    def _param(self, key, default=''):
        return self.icp.get_param('sembly.%s' % key, default)

    class _FakeClient:
        """Serves meetings by date, and counts the calls it receives."""

        def __init__(self, by_day=None, cap_day=None):
            self.token = 'tok'
            self.by_day = by_day or {}
            self.cap_day = cap_day
            self.windows = []
            self.detail_calls = 0

        def list_meetings(self, from_date=None, to_date=None, limit=200, **kw):
            self.windows.append((from_date, to_date))
            if self.cap_day and from_date == to_date == self.cap_day:
                return [dict(fixtures.LIST_MEETINGS_META, id=900000 + i)
                        for i in range(limit)]
            out = []
            start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
            for day, metas in self.by_day.items():
                if start <= day <= end:
                    out.extend(metas)
            return out

        def get_meeting(self, meeting_id):
            self.detail_calls += 1
            return dict(fixtures.GET_MEETING_DETAILS)

    def _run_with(self, client):
        return patch.object(type(self.Meeting), '_get_client', return_value=client)

    # ------------------------------------------------------------------ arming
    def test_starting_the_backfill_arms_the_cron(self):
        self.assertFalse(self.cron.active, "the cron must ship disabled")
        self.Meeting._start_backfill(date_from=date(2026, 1, 1), date_to=date(2026, 8, 1))
        self.assertTrue(self.cron.active)
        self.assertEqual(self._param('backfill_state'), 'running')
        self.assertEqual(self._param('backfill_cursor'), '2026-08-01')
        self.assertEqual(self._param('backfill_floor'), '2026-01-01')

    def test_the_cron_does_nothing_until_it_is_armed(self):
        self.icp.set_param('sembly.backfill_state', 'idle')
        client = self._FakeClient()
        with self._run_with(client):
            self.Meeting._cron_backfill_history()
        self.assertEqual(client.windows, [], "an unarmed backfill must not call Sembly")

    # ------------------------------------------------------------------ walking
    def test_it_walks_backwards_and_imports(self):
        day = date.today() - timedelta(days=3)
        client = self._FakeClient(by_day={day: [dict(fixtures.LIST_MEETINGS_META, id=77001)]})
        self.Meeting._start_backfill(date_from=date.today() - timedelta(days=20))
        with self._run_with(client):
            self.Meeting._cron_backfill_history()
        self.assertTrue(self.Meeting.search([('sembly_meeting_id', '=', '77001')]))
        self.assertTrue(client.windows, "it must have listed at least one window")
        # Windows move backwards in time, never forwards.
        starts = [w[0] for w in client.windows]
        self.assertEqual(starts, sorted(starts, reverse=True))

    def test_it_resumes_from_the_cursor(self):
        self.Meeting._start_backfill(date_from=date(2020, 1, 1), date_to=date(2026, 8, 1))
        client = self._FakeClient()
        self.icp.set_param('sembly.backfill_seconds', '0')  # one window per run
        with self._run_with(client):
            self.Meeting._cron_backfill_history()
            first = self._param('backfill_cursor')
            self.Meeting._cron_backfill_history()
            second = self._param('backfill_cursor')
        self.assertLess(date.fromisoformat(second), date.fromisoformat(first),
                        "the second run must continue further back, not restart")

    def test_it_stops_at_the_floor_and_stays_armed(self):
        """Reaching the floor ends the backfill in the PARAMETER, and leaves the
        cron alone.

        It used to assert the cron was disarmed, which is what wedged
        production: a job cannot write its own ir_cron row. The armed cron is
        now a one-parameter-read no-op, and disarming belongs to the wizard.
        """
        floor = date.today() - timedelta(days=3)
        self.Meeting._start_backfill(date_from=floor)
        with self._run_with(self._FakeClient()):
            self.Meeting._cron_backfill_history()
        self.assertEqual(self._param('backfill_state'), 'done')
        self.assertTrue(self.cron.active,
                        "the cron stays armed; a finished run is a cheap no-op")

    def test_it_stops_when_history_runs_dry(self):
        self.icp.set_param('sembly.backfill_empty_windows', '2')
        self.Meeting._start_backfill()          # no floor: only emptiness stops it
        with self._run_with(self._FakeClient()):
            self.Meeting._cron_backfill_history()
        self.assertEqual(self._param('backfill_state'), 'done')

    # ------------------------------------------------------------------ safety
    def test_a_capped_window_is_narrowed_rather_than_truncated(self):
        """A window returning exactly the server cap is indistinguishable from a
        truncated one, so accepting it would skip meetings silently."""
        client = self._FakeClient()
        end = date.today()
        original_days = (end - (end - timedelta(days=8))).days

        def list_meetings(from_date=None, to_date=None, limit=200, **kw):
            client.windows.append((from_date, to_date))
            span = (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days
            if span > 2:
                return [dict(fixtures.LIST_MEETINGS_META, id=800000 + i)
                        for i in range(limit)]
            return []

        client.list_meetings = list_meetings
        listed, start, truncated = self.Meeting._backfill_list_window(
            client, end - timedelta(days=8), end)
        self.assertFalse(truncated)
        self.assertLess((end - start).days, original_days,
                        "the window must have been narrowed")

    def test_an_unresolvable_single_day_is_reported_not_hidden(self):
        today = date.today().isoformat()
        client = self._FakeClient(cap_day=today)
        listed, start, truncated = self.Meeting._backfill_list_window(
            client, date.today(), date.today())
        self.assertTrue(truncated, "a full single day must be flagged, not swallowed")

    def test_a_network_failure_leaves_the_cursor_alone_to_retry(self):
        from odoo.addons.era_sembly_meetings.services.sembly_mcp_client import SemblyMcpError
        self.Meeting._start_backfill(date_from=date(2020, 1, 1), date_to=date(2026, 8, 1))
        before = self._param('backfill_cursor')
        client = self._FakeClient()

        def boom(**kw):
            raise SemblyMcpError("provider down")
        client.list_meetings = boom

        with self._run_with(client):
            self.Meeting._cron_backfill_history()   # must not raise
        self.assertEqual(self._param('backfill_cursor'), before,
                         "a failed window must be retried, not skipped over")
        self.assertEqual(self._param('backfill_state'), 'running')

    def test_details_are_not_refetched_for_meetings_we_already_summarised(self):
        meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        self.assertTrue(meeting.has_summary)
        day = date.today() - timedelta(days=1)
        client = self._FakeClient(by_day={day: [fixtures.LIST_MEETINGS_META]})
        self.Meeting._start_backfill(date_from=date.today() - timedelta(days=3))
        with self._run_with(client):
            self.Meeting._cron_backfill_history()
        self.assertEqual(client.detail_calls, 0,
                         "re-running a backfill must not pay for details twice")

    def test_a_dense_window_resumes_where_it_stopped(self):
        """Four ticks sat on one dense week because each re-listed the window
        from its start. The offset makes progress cumulative."""
        day = date.today() - timedelta(days=2)
        metas = [dict(fixtures.LIST_MEETINGS_META, id=930000 + i)
                 for i in range(6)]
        client = self._FakeClient(by_day={day: metas})
        self.Meeting._start_backfill(date_from=date.today() - timedelta(days=30))
        self.icp.set_param('sembly.backfill_seconds', '0')  # one meeting per tick

        with self._run_with(client):
            self.Meeting._cron_ai_match_queue  # noqa: B018 - registry warm-up
            self.Meeting._cron_backfill_history()
            first = int(self._param('backfill_offset', '0'))
            self.Meeting._cron_backfill_history()
            second = int(self._param('backfill_offset', '0'))
        self.assertGreater(first, 0, "the tick must record its position")
        self.assertGreater(second, first,
                           "the next tick must continue, not restart the window")

    def test_the_offset_clears_when_the_window_completes(self):
        self.icp.set_param('sembly.backfill_offset', '3')
        self.Meeting._start_backfill(date_from=date.today() - timedelta(days=10))
        with self._run_with(self._FakeClient()):
            self.Meeting._cron_backfill_history()
        self.assertEqual(self._param('backfill_offset', '0'), '0')

    def test_the_cron_never_writes_its_own_ir_cron_row(self):
        """THE rule this file exists to protect.

        A cron worker holds FOR NO KEY UPDATE on its own ir_cron row in a
        transaction SEPARATE from the one the job runs in, so a job that writes
        that row waits on a lock its own worker holds — forever. It wedged
        production: the backfill hung holding locks, which blocked module
        installs and every other cron operation until a restart.

        Odoo's ir_cron.write() raises a UserError to prevent exactly this, so
        the test makes any write to the row fail loudly and asserts the cron
        completes anyway.
        """
        floor = date.today() - timedelta(days=3)
        self.Meeting._start_backfill(date_from=floor)

        def explode(records, vals):
            raise AssertionError(
                "the cron must never write ir_cron: %s" % sorted(vals))

        with self._run_with(self._FakeClient()), \
                patch.object(type(self.cron), 'write', explode):
            self.Meeting._cron_backfill_history()      # must not raise

        self.assertEqual(self._param('backfill_state'), 'done')

    def test_a_finished_backfill_is_a_cheap_no_op_not_a_write(self):
        """Left armed on purpose. The next tick returns immediately."""
        self.icp.set_param('sembly.backfill_state', 'done')
        client = self._FakeClient()

        def explode(records, vals):
            raise AssertionError("no ir_cron write allowed here either")

        with self._run_with(client), \
                patch.object(type(self.cron), 'write', explode):
            self.Meeting._cron_backfill_history()
        self.assertEqual(client.windows, [], "a finished backfill must not call Sembly")

    def test_disarming_is_available_outside_a_cron(self):
        """The wizard runs in an HTTP request, which holds no cron lock."""
        self.Meeting._start_backfill(date_from=date.today() - timedelta(days=3))
        self.assertTrue(self.cron.active)
        self.env['sembly.import.wizard'].create({}).action_stop_backfill()
        self.cron.invalidate_recordset(['active'])
        self.assertFalse(self.cron.active)
        self.assertEqual(self._param('backfill_state'), 'idle')

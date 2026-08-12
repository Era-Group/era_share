# -*- coding: utf-8 -*-
"""The two groups.

    Employee — reads every meeting, files it against a record, and nothing else.
    Manager   — that, plus editing, creating and deleting.

Each of the three employee restrictions is enforced somewhere different, so
each is asserted through the real ORM as that user, never by inspecting
configuration:

    which records  -> the record rule
    create/delete  -> ir.model.access.csv
    which fields   -> sembly.meeting._check_content_access
"""
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblySecurity(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['res.users'].create({
            'name': "Sembly employee", 'login': 'sembly_employee_test',
            'group_ids': [(4, cls.env.ref('base.group_user').id),
                          (4, cls.env.ref('era_sembly_meetings.group_sembly_user').id)],
        })
        cls.manager = cls.env['res.users'].create({
            'name': "Sembly manager", 'login': 'sembly_manager_test',
            'group_ids': [(4, cls.env.ref('base.group_user').id),
                          (4, cls.env.ref('era_sembly_meetings.group_sembly_manager').id)],
        })

    def setUp(self):
        super().setUp()
        self.meeting = self.env['sembly.meeting']._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)

    # ------------------------------------------------------------------ groups
    def test_every_internal_user_is_a_sembly_employee(self):
        """base.group_user implies the employee group, so the app is usable by
        the whole company without an admin handing out access."""
        somebody = self.env['res.users'].create({
            'name': "Plain user", 'login': 'sembly_plain_test',
            'group_ids': [(4, self.env.ref('base.group_user').id)]})
        self.assertTrue(somebody.has_group('era_sembly_meetings.group_sembly_user'))
        self.assertFalse(somebody.has_group('era_sembly_meetings.group_sembly_manager'))

    def test_manager_implies_employee(self):
        self.assertTrue(self.manager.has_group('era_sembly_meetings.group_sembly_user'))

    # ------------------------------------------------------------------ reading
    def test_employee_reads_every_meeting(self):
        """Not only their own: a meeting is useful once whoever recognises what
        it was about can find it."""
        as_employee = self.meeting.with_user(self.employee)
        as_employee.invalidate_recordset()
        self.assertEqual(as_employee.name, "Acme ERP rollout - kickoff")
        self.assertIn(self.meeting, self.env['sembly.meeting'].with_user(
            self.employee).search([]))

    def test_employee_reads_the_items(self):
        items = self.env['sembly.meeting.item'].with_user(self.employee).search(
            [('meeting_id', '=', self.meeting.id)])
        self.assertEqual(len(items), 7)

    # ------------------------------------------------------------------ linking
    def test_employee_may_set_a_link_field(self):
        """The one thing an employee is meant to do. Uses whichever link field
        the installed satellites provide, so this passes in any combination."""
        link_fields = self.env['sembly.meeting']._sembly_link_fields()
        if not link_fields:
            self.skipTest("no link module installed")
        field = sorted(link_fields)[0]
        comodel = self.env['sembly.meeting']._fields[field].comodel_name
        target = self.env[comodel].sudo().search([], limit=1)
        if not target:
            self.skipTest("no %s record to link to" % comodel)

        self.meeting.with_user(self.employee).write({field: target.id})
        self.assertEqual(self.meeting[field], target)
        # ...and doing so still claims the record against the matcher.
        self.assertEqual(self.meeting.link_state, 'manual')

    def test_employee_may_bulk_set_a_link_field(self):
        """The list view's multi_edit issues ONE write on the whole selection —
        this is the server-side shape of "select rows, set the opportunity"."""
        link_fields = self.env['sembly.meeting']._sembly_link_fields()
        if not link_fields:
            self.skipTest("no link module installed")
        field = sorted(link_fields)[0]
        comodel = self.env['sembly.meeting']._fields[field].comodel_name
        target = self.env[comodel].sudo().search([], limit=1)
        if not target:
            self.skipTest("no %s record to link to" % comodel)

        batch = self.env['sembly.meeting']
        for i in range(3):
            batch |= self.env['sembly.meeting']._upsert_from_mcp(
                dict(fixtures.LIST_MEETINGS_META, id=610000 + i,
                     title="Bulk %s" % i))

        batch.with_user(self.employee).write({field: target.id})
        for meeting in batch:
            self.assertEqual(meeting[field], target)
            # Each record is individually claimed against the matcher.
            self.assertEqual(meeting.link_state, 'manual')

    def test_bulk_content_edit_is_refused_for_the_whole_batch(self):
        """multi_edit on a content column must fail atomically: no record in
        the selection may slip through."""
        batch = self.meeting
        batch |= self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=620001, title="Second"))
        with self.assertRaises(AccessError):
            batch.with_user(self.employee).write({'name': "bulk renamed"})
        batch.invalidate_recordset()
        self.assertNotIn("bulk renamed", batch.mapped('name'))

    # ------------------------------------------------------------------ content
    def test_employee_cannot_edit_the_content(self):
        for field, value in (('name', "Renamed by an employee"),
                             ('summary', "<p>rewritten</p>"),
                             ('transcript', "fabricated"),
                             ('started_at', '2026-01-01 00:00:00')):
            with self.subTest(field=field):
                with self.assertRaises(AccessError):
                    self.meeting.with_user(self.employee).write({field: value})
        self.meeting.invalidate_recordset()
        self.assertEqual(self.meeting.name, "Acme ERP rollout - kickoff")

    def test_manager_can_edit_the_content(self):
        self.meeting.with_user(self.manager).write({'name': "Renamed by a manager"})
        self.assertEqual(self.meeting.name, "Renamed by a manager")

    def test_the_sync_channels_are_not_blocked_by_the_content_lock(self):
        """The webhook, the MCP sync and the matcher all run elevated — they are
        precisely what is supposed to write the content."""
        meeting = self.env['sembly.meeting'].with_user(self.employee).sudo(
            )._upsert_from_webhook(fixtures.WEBHOOK_TRANSCRIPTION, 'transcription')
        self.assertTrue(meeting.has_transcript)

    def test_can_edit_content_flag_matches_the_server_rule(self):
        """The views key their readonly state off this; it must not disagree
        with what _check_content_access actually enforces."""
        self.assertFalse(self.meeting.with_user(self.employee).can_edit_content)
        self.assertTrue(self.meeting.with_user(self.manager).can_edit_content)

    # ------------------------------------------------------------------ lifecycle
    def test_employee_cannot_create_a_meeting(self):
        """Meetings come from Sembly; creating one by hand is authoring content."""
        with self.assertRaises(AccessError):
            self.env['sembly.meeting'].with_user(self.employee).create({
                'sembly_meeting_id': 'made-up-1', 'name': "Invented"})

    def test_employee_cannot_delete_a_meeting(self):
        with self.assertRaises(AccessError):
            self.meeting.with_user(self.employee).unlink()

    def test_manager_can_create_and_delete(self):
        meeting = self.env['sembly.meeting'].with_user(self.manager).create({
            'sembly_meeting_id': 'mgr-made-1', 'name': "By a manager"})
        self.assertTrue(meeting.exists())
        meeting.unlink()
        self.assertFalse(meeting.exists())

    # ------------------------------------------------------------------ config
    def test_employee_cannot_read_the_sync_log(self):
        with self.assertRaises(AccessError):
            self.env['sembly.sync.log'].with_user(self.employee).search([])

    def test_employee_cannot_run_the_import_wizard(self):
        with self.assertRaises(AccessError):
            self.env['sembly.import.wizard'].with_user(self.employee).create({})

    def test_manager_can_run_the_import_wizard(self):
        wizard = self.env['sembly.import.wizard'].with_user(self.manager).create({})
        self.assertTrue(wizard)

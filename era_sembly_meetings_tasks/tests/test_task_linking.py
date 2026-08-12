# -*- coding: utf-8 -*-
"""The project and task links, and the bucket-task fallback.

The base app's suite already proves the matching machinery; this one proves
this module's own rules: a task implies its project, a project-only match gets
exactly one bucket task, and a more specific link elsewhere suppresses it.
"""
import json
from unittest.mock import patch

from odoo.addons.era_sembly_meetings.tests import fixtures
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyTaskLinking(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.partner = self.env['res.partner'].create({
            'name': "Acme Industrial", 'email': "sara@acme-test.com"})
        self.project = self.env['project.project'].create({
            'name': "Acme ERP implementation", 'partner_id': self.partner.id})
        self.task = self.env['project.task'].create({
            'name': "Acme data migration", 'project_id': self.project.id})
        self.meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)

    def _reply(self, **kwargs):
        payload = {'project_id': None, 'task_id': None,
                   'confidence': 0.0, 'reason': "test"}
        payload.update(kwargs)
        return json.dumps(payload)

    def _answer(self, reply):
        return patch.object(type(self.Meeting), '_ask_agent', return_value=reply)

    def _bucket_count(self):
        return self.env['project.task'].search_count([
            ('project_id', '=', self.project.id), ('name', '=', "الاجتماعات")])

    # ------------------------------------------------------------------ pools
    def test_both_pools_are_contributed(self):
        pools, basis = self.meeting._collect_candidates()
        keys = [pool['key'] for pool in pools]
        self.assertIn('project_id', keys)
        self.assertIn('task_id', keys)
        self.assertIn('projects(', basis)
        self.assertIn('tasks(', basis)

    def test_pools_are_ordered_project_then_task(self):
        pools, _basis = self.meeting._collect_candidates()
        keys = [pool['key'] for pool in pools]
        self.assertLess(keys.index('project_id'), keys.index('task_id'))

    def test_open_tasks_are_in_the_ambient_pool(self):
        pools, _basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'task_id')
        self.assertIn(self.task, pool['records'])

    def test_pools_are_capped(self):
        from ..models.sembly_meeting import MAX_PROJECT_CANDIDATES, MAX_TASK_CANDIDATES
        pools, _basis = self.meeting._collect_candidates()
        by_key = {pool['key']: pool['records'] for pool in pools}
        self.assertLessEqual(len(by_key['project_id']), MAX_PROJECT_CANDIDATES)
        self.assertLessEqual(len(by_key['task_id']), MAX_TASK_CANDIDATES)

    # ------------------------------------------------------------------ linking
    def test_task_match_infers_its_project(self):
        """The model returning only task_id must still produce a complete link."""
        with self._answer(self._reply(task_id=self.task.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.task_id, self.task)
        self.assertEqual(self.meeting.project_id, self.project)
        self.assertEqual(self._bucket_count(), 0)

    def test_hallucinated_task_is_rejected(self):
        bogus = max(self.env['project.task'].search([]).ids) + 10000
        with self._answer(self._reply(task_id=bogus, confidence=0.99)):
            self.meeting._ai_match()
        self.assertFalse(self.meeting.task_id)
        self.assertEqual(self.meeting.link_state, 'unlinked')

    def test_hand_picked_task_is_never_overwritten(self):
        self.meeting.task_id = self.task
        self.assertEqual(self.meeting.link_state, 'manual')
        with self._answer(self._reply(task_id=self.task.id, confidence=0.99)) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()

    # ------------------------------------------------------------------ bucket
    def test_project_only_creates_the_bucket_task_once(self):
        with self._answer(self._reply(project_id=self.project.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.project_id, self.project)
        bucket = self.meeting.task_id
        self.assertTrue(bucket)
        self.assertEqual(bucket.name, "الاجتماعات")
        self.assertEqual(bucket.project_id, self.project)

        second = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=fixtures.MEETING_ID + 1,
                 title="Acme follow-up"))
        with self._answer(self._reply(project_id=self.project.id, confidence=0.9)):
            second._ai_match()
        # Reused, not duplicated.
        self.assertEqual(second.task_id, bucket)
        self.assertEqual(self._bucket_count(), 1)

    def test_bucket_is_not_created_below_the_threshold(self):
        """A suggestion has linked nothing, so it must not create a task."""
        with self._answer(self._reply(project_id=self.project.id, confidence=0.4)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'suggested')
        self.assertEqual(self._bucket_count(), 0)

    def test_bucket_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.auto_create_meetings_task', '0')
        try:
            with self._answer(self._reply(project_id=self.project.id, confidence=0.9)):
                self.meeting._ai_match()
            self.assertEqual(self.meeting.project_id, self.project)
            self.assertFalse(self.meeting.task_id)
            self.assertEqual(self._bucket_count(), 0)
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'sembly.auto_create_meetings_task', '1')

    def test_bucket_respects_a_custom_name(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.meetings_task_name', "Meetings")
        try:
            with self._answer(self._reply(project_id=self.project.id, confidence=0.9)):
                self.meeting._ai_match()
            self.assertEqual(self.meeting.task_id.name, "Meetings")
        finally:
            self.env['ir.config_parameter'].sudo().set_param(
                'sembly.meetings_task_name', "الاجتماعات")

    def test_external_link_suppresses_the_bucket(self):
        """When another module (crm, tickets) has claimed the meeting, a project
        match must not also file it under a bucket task."""
        with patch.object(type(self.Meeting), '_has_external_link',
                          return_value=True), \
                self._answer(self._reply(project_id=self.project.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.project_id, self.project)
        self.assertFalse(self.meeting.task_id)
        self.assertEqual(self._bucket_count(), 0)

    # ------------------------------------------------------------------ chatter
    def test_task_receives_the_summary_note_and_project_does_not(self):
        """A project-only match already lands on the bucket task, so posting on
        the project too would duplicate the note."""
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'project_id': self.project.id, 'task_id': self.task.id})
        targets = self.meeting._summary_targets()
        self.assertIn(self.task, targets)
        self.assertNotIn(self.project, targets)

        self.meeting.action_post_summary_to_chatter()
        # Only the notes THIS feature posted: a target record carries chatter
        # of its own, so counting every internal note would measure Odoo.
        note = self.env.ref('mail.mt_note')
        notes = self.task.message_ids.filtered(
            lambda m: m.subtype_id == note and "Meeting summary" in (m.body or ''))
        self.assertEqual(len(notes), 1)
        self.assertIn("September", notes.body)

    # ------------------------------------------------------------------ buttons
    def test_smart_button_counts(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'project_id': self.project.id, 'task_id': self.task.id})
        self.task.invalidate_recordset()
        self.project.invalidate_recordset()
        self.assertEqual(self.task.sembly_meeting_count, 1)
        self.assertEqual(self.project.sembly_meeting_count, 1)

    def test_project_count_folds_in_meetings_on_its_tasks(self):
        """A meeting attached only to a task must still show on its project."""
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'task_id': self.task.id})
        self.project.invalidate_recordset()
        self.assertEqual(self.project.sembly_meeting_count, 1)

    def test_open_linked_actions(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'project_id': self.project.id, 'task_id': self.task.id})
        project_action = self.meeting.action_open_linked_project()
        task_action = self.meeting.action_open_linked_task()
        self.assertEqual(project_action['res_model'], 'project.project')
        self.assertEqual(project_action['res_id'], self.project.id)
        self.assertEqual(task_action['res_model'], 'project.task')
        self.assertEqual(task_action['res_id'], self.task.id)

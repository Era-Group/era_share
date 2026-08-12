# -*- coding: utf-8 -*-
"""The helpdesk ticket link."""
import json
from unittest.mock import patch

from odoo.addons.era_sembly_meetings.tests import fixtures
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyTicketLinking(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.partner = self.env['res.partner'].create({
            'name': "Acme Industrial", 'email': "sara@acme-test.com"})
        self.team = self.env['helpdesk.team'].create({'name': "Acme support"})
        self.ticket = self.env['helpdesk.ticket'].create({
            'name': "Acme ERP login failure",
            'team_id': self.team.id,
            'partner_id': self.partner.id,
        })
        self.meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)

    def _reply(self, **kwargs):
        payload = {'ticket_id': None, 'confidence': 0.0, 'reason': "test"}
        payload.update(kwargs)
        return json.dumps(payload)

    def _answer(self, reply):
        return patch.object(type(self.Meeting), '_ask_agent', return_value=reply)

    # ------------------------------------------------------------------ pool
    def test_pool_is_contributed_and_rendered(self):
        pools, basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'ticket_id')
        self.assertIn(self.ticket, pool['records'])
        self.assertIn('tickets(', basis)
        self.assertIn("Acme ERP login failure", pool['render'](self.ticket))

    def test_pool_is_last_in_the_prompt(self):
        """Prompt block order must be stable: tickets come after the rest."""
        pools, _basis = self.meeting._collect_candidates()
        self.assertEqual(pools[-1]['key'], 'ticket_id')

    def test_pool_is_capped(self):
        from ..models.sembly_meeting import MAX_TICKET_CANDIDATES
        pools, _basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'ticket_id')
        self.assertLessEqual(len(pool['records']), MAX_TICKET_CANDIDATES)

    # ------------------------------------------------------------------ linking
    def test_high_confidence_links_the_ticket(self):
        with self._answer(self._reply(ticket_id=self.ticket.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'auto')
        self.assertEqual(self.meeting.ticket_id, self.ticket)

    def test_hallucinated_ticket_is_rejected(self):
        bogus = max(self.env['helpdesk.ticket'].search([]).ids) + 10000
        with self._answer(self._reply(ticket_id=bogus, confidence=0.99)):
            self.meeting._ai_match()
        self.assertFalse(self.meeting.ticket_id)
        self.assertEqual(self.meeting.link_state, 'unlinked')

    def test_hand_picked_ticket_is_never_overwritten(self):
        self.meeting.ticket_id = self.ticket
        self.assertEqual(self.meeting.link_state, 'manual')
        with self._answer(self._reply(ticket_id=self.ticket.id, confidence=0.99)) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()

    # ------------------------------------------------------------------ seams
    def test_linked_ticket_counts_as_an_external_link(self):
        self.assertFalse(self.meeting._has_external_link())
        self.meeting.ticket_id = self.ticket
        self.assertTrue(self.meeting._has_external_link())

    def test_ticket_receives_the_summary_note(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'ticket_id': self.ticket.id})
        self.assertIn(self.ticket, self.meeting._summary_targets())
        self.meeting.action_post_summary_to_chatter()
        # Only the notes THIS feature posted: a target record carries chatter
        # of its own, so counting every internal note would measure Odoo.
        note = self.env.ref('mail.mt_note')
        notes = self.ticket.message_ids.filtered(
            lambda m: m.subtype_id == note and "Meeting summary" in (m.body or ''))
        self.assertEqual(len(notes), 1)
        self.assertIn("September", notes.body)

    # ------------------------------------------------------------------ button
    def test_smart_button_count_and_action(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'ticket_id': self.ticket.id})
        self.ticket.invalidate_recordset()
        self.assertEqual(self.ticket.sembly_meeting_count, 1)
        action = self.ticket.action_view_sembly_meetings()
        self.assertEqual(action['res_model'], 'sembly.meeting')

    def test_open_linked_ticket_action(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'ticket_id': self.ticket.id})
        action = self.meeting.action_open_linked_ticket()
        self.assertEqual(action['res_model'], 'helpdesk.ticket')
        self.assertEqual(action['res_id'], self.ticket.id)

# -*- coding: utf-8 -*-
"""The opportunity link.

The base app's suite already proves the matching machinery; this one proves
that THIS module plugs into it correctly — the pool is contributed and capped,
the field is guarded, the note is posted on the opportunity, and the smart
button counts.
"""
import json
from unittest.mock import patch

from odoo.addons.era_sembly_meetings.tests import fixtures
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyCrmLinking(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.partner = self.env['res.partner'].create({
            'name': "Acme Industrial", 'email': "sara@acme-test.com"})
        self.lead = self.env['crm.lead'].create({
            'name': "Acme ERP rollout", 'partner_id': self.partner.id})
        self.meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)

    def _reply(self, **kwargs):
        payload = {'lead_id': None, 'confidence': 0.0, 'reason': "test"}
        payload.update(kwargs)
        return json.dumps(payload)

    def _answer(self, reply):
        return patch.object(type(self.Meeting), '_ask_agent', return_value=reply)

    # ------------------------------------------------------------------ pool
    def test_pool_is_contributed_and_rendered(self):
        pools, basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'lead_id')
        self.assertIn(self.lead, pool['records'])
        self.assertIn('leads(', basis)
        rendered = pool['render'](self.lead)
        self.assertIn(str(self.lead.id), rendered)
        self.assertIn("Acme ERP rollout", rendered)

    def test_pool_found_via_participants(self):
        """A meeting with no matching title word still finds the customer's
        opportunity through the participants."""
        self.meeting.sudo().write({
            'name': "Weekly sync", 'partner_ids': [(6, 0, self.partner.ids)]})
        pools, _basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'lead_id')
        self.assertIn(self.lead, pool['records'])

    def test_pool_is_capped(self):
        from ..models.sembly_meeting import MAX_LEAD_CANDIDATES
        pools, _basis = self.meeting._collect_candidates()
        pool = next(p for p in pools if p['key'] == 'lead_id')
        self.assertLessEqual(len(pool['records']), MAX_LEAD_CANDIDATES)

    def test_transcript_is_never_sent_to_the_llm(self):
        """PDPL data minimisation still holds once a pool exists."""
        self.meeting.sudo().write({'transcript': "SECRET-TRANSCRIPT-MARKER"})
        pools, _basis = self.meeting._collect_candidates()
        prompt = self.meeting._build_match_prompt(pools)
        self.assertNotIn("SECRET-TRANSCRIPT-MARKER", prompt)
        self.assertIn("CANDIDATE OPPORTUNITIES", prompt)

    # ------------------------------------------------------------------ linking
    def test_high_confidence_links_the_opportunity(self):
        with self._answer(self._reply(lead_id=self.lead.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'auto')
        self.assertEqual(self.meeting.lead_id, self.lead)

    def test_low_confidence_links_nothing(self):
        with self._answer(self._reply(lead_id=self.lead.id, confidence=0.4)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'suggested')
        self.assertFalse(self.meeting.lead_id)

    def test_hallucinated_lead_is_rejected(self):
        bogus = max(self.env['crm.lead'].search([]).ids) + 10000
        with self._answer(self._reply(lead_id=bogus, confidence=0.99)):
            self.meeting._ai_match()
        self.assertFalse(self.meeting.lead_id)
        self.assertEqual(self.meeting.link_state, 'unlinked')

    def test_hand_picked_opportunity_is_never_overwritten(self):
        self.meeting.lead_id = self.lead
        self.assertEqual(self.meeting.link_state, 'manual')
        with self._answer(self._reply(lead_id=self.lead.id, confidence=0.99)) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()
        self.assertEqual(self.meeting.link_state, 'manual')

    def test_sync_write_does_not_claim_the_record(self):
        self.meeting.with_context(sembly_sync=True).write({'lead_id': self.lead.id})
        self.assertNotEqual(self.meeting.link_state, 'manual')

    # ------------------------------------------------------------------ seams
    def test_linked_lead_counts_as_an_external_link(self):
        """So era_sembly_meetings_tasks does not also file it under a project
        bucket task."""
        self.assertFalse(self.meeting._has_external_link())
        self.meeting.lead_id = self.lead
        self.assertTrue(self.meeting._has_external_link())

    def test_opportunity_receives_the_summary_note(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'lead_id': self.lead.id})
        self.assertIn(self.lead, self.meeting._summary_targets())
        self.meeting.action_post_summary_to_chatter()
        # Only the notes THIS feature posted: a target record carries chatter
        # of its own, so counting every internal note would measure Odoo.
        note = self.env.ref('mail.mt_note')
        notes = self.lead.message_ids.filtered(
            lambda m: m.subtype_id == note and "Meeting summary" in (m.body or ''))
        self.assertEqual(len(notes), 1)
        self.assertIn("September", notes.body)

    # ------------------------------------------------------------------ button
    def test_smart_button_count_and_action(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'lead_id': self.lead.id})
        self.lead.invalidate_recordset()
        self.assertEqual(self.lead.sembly_meeting_count, 1)
        action = self.lead.action_view_sembly_meetings()
        self.assertEqual(action['res_model'], 'sembly.meeting')
        self.assertEqual(action['domain'], [('lead_id', '=', self.lead.id)])

    def test_open_linked_lead_action(self):
        self.meeting.sudo().with_context(sembly_sync=True).write({
            'lead_id': self.lead.id})
        action = self.meeting.action_open_linked_lead()
        self.assertEqual(action['res_model'], 'crm.lead')
        self.assertEqual(action['res_id'], self.lead.id)

    # ------------------------------------------------------- internal meetings
    def _fallback_to(self, lead):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.internal_fallback_ref', 'crm.lead,%s' % lead.id)

    def test_crm_offers_the_opportunity_as_a_fallback_target(self):
        """The picker is built from what the installed satellites contribute,
        so the base never names crm.lead itself."""
        self.assertEqual(
            self.env['sembly.meeting']._sembly_link_field_by_model().get('crm.lead'),
            'lead_id')

    def test_an_internal_meeting_lands_on_the_configured_opportunity(self):
        bucket = self.env['crm.lead'].create({'name': "Internal meetings"})
        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=880001, title="Weekly stand-up"))
        meeting.sudo().write({'participant_emails': "someone@gmail.com"})
        self._fallback_to(bucket)

        with patch.object(type(meeting), '_ai_candidate_pools', return_value=[]), \
                patch.object(type(meeting), '_ask_agent',
                             return_value='{"internal": true, "reason": "team stand-up"}'):
            meeting._ai_match()

        self.assertEqual(meeting.lead_id, bucket)
        self.assertEqual(meeting.link_state, 'auto')

    def test_a_customer_meeting_never_lands_there(self):
        """No external domain is not proof — a customer call from a personal
        address, or one with no emails at all, must still be caught."""
        bucket = self.env['crm.lead'].create({'name': "Internal meetings"})
        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=880002, title="Acme pricing"))
        self._fallback_to(bucket)

        with patch.object(type(meeting), '_ai_candidate_pools', return_value=[]), \
                patch.object(type(meeting), '_ask_agent',
                             return_value='{"internal": false, "reason": "about a client"}'):
            meeting._ai_match()

        self.assertFalse(meeting.lead_id)
        self.assertEqual(meeting.link_state, 'unlinked')

    def test_an_external_attendee_blocks_it_before_any_llm_call(self):
        bucket = self.env['crm.lead'].create({'name': "Internal meetings"})
        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=880003, title="Sync"))
        meeting.sudo().write({'participant_emails': "buyer@acme-industrial.com"})
        self._fallback_to(bucket)

        with patch.object(type(meeting), '_ai_candidate_pools', return_value=[]), \
                patch.object(type(meeting), '_ask_agent',
                             return_value='{"internal": true}') as ask:
            meeting._ai_match()

        ask.assert_not_called()
        self.assertFalse(meeting.lead_id)

    # ---------------------------------------------------------- archived leads
    def test_an_archived_opportunity_can_be_linked_by_hand(self):
        """An old meeting usually belongs to an opportunity that has since been
        archived, so the field must accept one."""
        lead = self.env['crm.lead'].create({'name': "Closed 2024 deal"})
        lead.action_archive()
        self.assertFalse(lead.active)

        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=890001))
        meeting.lead_id = lead
        self.assertEqual(meeting.lead_id, lead)
        self.assertEqual(meeting.link_state, 'manual')

    def test_the_picker_offers_archived_opportunities(self):
        """name_search drops archived records unless active_test is off — which
        is what the view's context supplies."""
        lead = self.env['crm.lead'].create({'name': "Zzz archived subject"})
        lead.action_archive()
        Lead = self.env['crm.lead']
        self.assertFalse(
            Lead.name_search("Zzz archived subject"),
            "sanity: archived records are hidden by default")
        self.assertTrue(
            Lead.with_context(active_test=False).name_search("Zzz archived subject"),
            "the view context must be able to surface them")

    def test_archived_leads_reach_the_matcher_as_candidates(self):
        partner = self.env['res.partner'].create(
            {'name': "Old Client", 'email': "ops@old-client-test.com"})
        archived = self.env['crm.lead'].create(
            {'name': "Old Client rollout", 'partner_id': partner.id})
        archived.action_archive()

        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=890002,
                 title="Old Client rollout review"))
        meeting.sudo().write({'partner_ids': [(6, 0, partner.ids)]})

        pool = next(p for p in meeting._ai_candidate_pools() if p['key'] == 'lead_id')
        self.assertIn(archived, pool['records'])
        self.assertIn('archived', pool['basis'])
        # The model is TOLD it is archived — that is the context that tells an
        # old meeting apart from a live one.
        self.assertIn('ARCHIVED', pool['render'](archived))

    def test_active_leads_are_never_crowded_out_by_archived_ones(self):
        """A live database carries far more archived leads than live ones, so
        searching both together would push the active ones out of the cap."""
        partner = self.env['res.partner'].create(
            {'name': "Busy Client", 'email': "ops@busy-client-test.com"})
        live = self.env['crm.lead'].create(
            {'name': "Busy Client live deal", 'partner_id': partner.id})
        for i in range(70):
            old = self.env['crm.lead'].create(
                {'name': "Busy Client old %s" % i, 'partner_id': partner.id})
            old.action_archive()

        meeting = self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=890003, title="Busy Client sync"))
        meeting.sudo().write({'partner_ids': [(6, 0, partner.ids)]})

        pool = next(p for p in meeting._ai_candidate_pools() if p['key'] == 'lead_id')
        self.assertIn(live, pool['records'], "the live opportunity must survive the cap")

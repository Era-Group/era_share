# -*- coding: utf-8 -*-
"""AI linking — the target-agnostic half (requirements 3 and 4).

The base app links to nothing, so these tests drive the machinery through the
``_ai_candidate_pools`` seam with a stand-in pool, and assert OUR logic:
hallucinated ids are rejected, the confidence gate works, a human decision is
never overwritten, the transcript never leaves the instance, and the pool order
does not depend on the module install order.

``company_id`` is the only many2one the base app owns, so it stands in for a
link field. Each link module tests its own real field on top of this.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from . import fixtures
from ..models.sembly_meeting import SemblyMeeting as BaseSemblyMeeting


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyMatching(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.company = self.env.company
        self.meeting = self.Meeting._upsert_from_mcp(
            fixtures.LIST_MEETINGS_META, fixtures.GET_MEETING_DETAILS)
        # The fixture is a recent meeting, so the upsert now queues it for the
        # delivery pipeline. This suite is about matching, not delivery: start
        # every case from an empty queue so what it asserts is its own doing.
        self.meeting.sudo().with_context(sembly_sync=True).write(
            {'ai_match_queued': False})

    # ------------------------------------------------------------------ helpers
    def _pool(self, records=None, key='company_id', sequence=10):
        records = self.company if records is None else records
        return {
            'key': key,
            'label': "CANDIDATE COMPANIES (id | name)",
            'records': records,
            'render': lambda rec: '%s | %s' % (rec.id, rec.name),
            'sequence': sequence,
            'basis': 'companies(%d)' % len(records),
        }

    def _with_pools(self, pools):
        return patch.object(type(self.Meeting), '_ai_candidate_pools',
                            lambda meeting: list(pools))

    def _reply(self, **kwargs):
        payload = {'company_id': None, 'confidence': 0.0, 'reason': "test"}
        payload.update(kwargs)
        return json.dumps(payload)

    def _answer(self, reply):
        return patch.object(type(self.Meeting), '_ask_agent', return_value=reply)

    # ------------------------------------------------------------------ gating
    def test_high_confidence_links_automatically(self):
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'auto')
        self.assertEqual(self.meeting.company_id, self.company)
        self.assertEqual(self.meeting.ai_confidence, 0.9)
        self.assertIn('companies', self.meeting.ai_matched_on)

    def test_low_confidence_only_suggests(self):
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.4,
                                         reason="not sure")):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'suggested')
        self.assertEqual(self.meeting.ai_reasoning, "not sure")

    def test_apply_suggestion_promotes_the_link(self):
        """The ids are re-validated against a fresh candidate set rather than
        trusted from a stale reasoning string."""
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.4)):
            self.meeting._ai_match()
            self.assertEqual(self.meeting.link_state, 'suggested')
            self.meeting.action_apply_ai_suggestion()
        self.assertEqual(self.meeting.link_state, 'auto')

    # ------------------------------------------------------------------ safety
    def test_hallucinated_id_is_rejected(self):
        """The model may only choose from what it was actually shown."""
        bogus = max(self.env['res.company'].search([]).ids) + 10000
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=bogus, confidence=0.99)):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'unlinked')

    def test_unparseable_reply_links_nothing(self):
        with self._with_pools([self._pool()]), \
                self._answer("I think it is the Acme deal, probably."):
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'unlinked')

    def test_no_pools_means_no_llm_call(self):
        """With no link module installed there is nothing to match against, and
        an LLM call would only burn credit."""
        with self._with_pools([]), self._answer(self._reply()) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()
        self.assertEqual(self.meeting.link_state, 'unlinked')
        self.assertEqual(self.meeting.ai_matched_on, 'no candidates')

    def test_empty_pools_means_no_llm_call(self):
        """A pool that narrowed down to nothing is the same situation."""
        empty = self._pool(records=self.env['res.company'].browse())
        with self._with_pools([empty]), self._answer(self._reply()) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()

    def test_manual_link_is_never_overwritten(self):
        """Requirement 3: the user can choose by hand in all cases — which is
        only true if the matcher then leaves the record alone."""
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.name = "Chosen by a human"
            self.assertEqual(self.meeting.link_state, 'manual')
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.99)) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()
        self.assertEqual(self.meeting.link_state, 'manual')

    def test_sync_writes_do_not_claim_the_record(self):
        """The upserts and the matcher itself must not trip the manual guard."""
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.with_context(sembly_sync=True).write({'name': "From Sembly"})
        self.assertNotEqual(self.meeting.link_state, 'manual')

    def test_force_overrides_manual(self):
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.name = "Chosen by a human"
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.95)) as ask:
            self.meeting.action_ai_match()
        ask.assert_called()

    def test_transcript_is_never_sent_to_the_llm(self):
        """PDPL data minimisation — asserted on the actual prompt text."""
        self.meeting.sudo().write({'transcript': "SECRET-TRANSCRIPT-MARKER"})
        with self._with_pools([self._pool()]):
            pools, _basis = self.meeting._collect_candidates()
            prompt = self.meeting._build_match_prompt(pools)
        self.assertNotIn("SECRET-TRANSCRIPT-MARKER", prompt)
        self.assertIn("Acme ERP rollout - kickoff", prompt)
        self.assertIn("CANDIDATE COMPANIES", prompt)
        self.assertIn('"company_id"', prompt)

    def test_pool_order_is_stable_regardless_of_registration_order(self):
        """The prompt must not change shape with the module install order."""
        first = self._pool(key='company_id', sequence=10)
        second = dict(self._pool(key='partner_id', sequence=20),
                      label="CANDIDATE PARTNERS (id | name)")
        with self._with_pools([second, first]):
            pools, _basis = self.meeting._collect_candidates()
        self.assertEqual([pool['key'] for pool in pools], ['company_id', 'partner_id'])

    # ------------------------------------------------------------------ seams
    def test_postprocess_seam_can_derive_a_link(self):
        derived = {}

        def postprocess(meeting, links, pools):
            derived.update(links)
            return dict(links, company_id=self.company.id)

        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ai_postprocess_links', postprocess), \
                self._answer(self._reply(confidence=0.9)):
            self.meeting._ai_match()
        self.assertEqual(derived, {'company_id': False})
        self.assertEqual(self.meeting.link_state, 'auto')

    def test_after_link_seam_runs_only_for_automatic_links(self):
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ai_after_link') as hook, \
                self._answer(self._reply(company_id=self.company.id, confidence=0.4)):
            self.meeting._ai_match()
        hook.assert_not_called()

        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ai_after_link') as hook, \
                self._answer(self._reply(company_id=self.company.id, confidence=0.9)):
            self.meeting._ai_match()
        hook.assert_called_once()

    def test_seam_contract_holds_for_every_installed_link_module(self):
        """The seams must stay well-formed whatever combination is installed.

        This deliberately does NOT assert that the base links to nothing: the
        test suite runs against a database where the link modules may well be
        installed, and asserting emptiness would only be testing which modules
        happen to be present. What must hold in every combination is the
        contract itself.
        """
        link_fields = self.Meeting._sembly_link_fields()
        self.assertIsInstance(link_fields, set)
        for name in link_fields:
            self.assertIn(name, self.Meeting._fields,
                          "%s is claimed as a link field but is not a field" % name)

        for pool in self.meeting._ai_candidate_pools():
            self.assertLessEqual({'key', 'label', 'records', 'render'}, set(pool))
            self.assertIn(pool['key'], self.Meeting._fields,
                          "pool %s does not correspond to a field" % pool['key'])
            self.assertIn(pool['key'], link_fields,
                          "pool %s is not guarded by _sembly_link_fields" % pool['key'])
            self.assertIsInstance(pool['render'](pool['records'][:1]) if pool['records']
                                  else '', str)

        self.assertIsInstance(self.meeting._summary_targets(), list)
        self.assertIsInstance(self.meeting._has_external_link(), bool)

    def test_base_itself_contributes_no_link(self):
        """The base implementations, called past any satellite override.

        This is the claim the whole split rests on: installed alone, this
        module links a meeting to nothing. Reaching the class defined in this
        module's own source bypasses the MRO, so the assertion stays true no
        matter which link modules the test database happens to carry.
        """
        self.assertEqual(BaseSemblyMeeting._sembly_link_fields(self.Meeting), set())
        self.assertEqual(BaseSemblyMeeting._ai_candidate_pools(self.meeting), [])
        self.assertEqual(BaseSemblyMeeting._summary_targets(self.meeting), [])
        self.assertFalse(BaseSemblyMeeting._has_external_link(self.meeting))

    # ------------------------------------------------------------------ narrowing
    def test_title_tokens_drop_stop_words(self):
        tokens = [t.lower() for t in self.meeting._title_tokens()]
        self.assertIn('acme', tokens)
        self.assertIn('rollout', tokens)
        # 'kickoff' survives; the generic words do not.
        self.assertNotIn('meeting', tokens)
        self.assertNotIn('the', tokens)

    def test_candidate_partners_include_commercial_parents(self):
        parent = self.env['res.partner'].create({'name': "Acme Group"})
        child = self.env['res.partner'].create({
            'name': "Sara Mansour", 'parent_id': parent.id,
            'email': "sara@acme-test.com"})
        self.meeting.sudo().write({'partner_ids': [(6, 0, child.ids)]})
        self.assertIn(parent, self.meeting._candidate_partners())
        self.assertIn(child, self.meeting._candidate_partners())

    # ------------------------------------------------------------------ queue
    def test_bulk_action_queues_and_never_touches_manual(self):
        """The list's bulk action must not repeat the form button's force: a
        sweep over a selection never overrides a human decision."""
        second = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=fixtures.MEETING_ID + 50,
                 title="Queued one"))
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.name = "Hand linked"          # -> manual
        batch = self.meeting | second

        action = batch.action_queue_ai_match()

        self.assertFalse(self.meeting.ai_match_queued, "manual must be skipped")
        self.assertTrue(second.ai_match_queued)
        self.assertEqual(action['params']['type'], 'success')
        self.assertIn('1', action['params']['message'])

    def test_bulk_action_on_only_manual_rows_warns(self):
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.name = "Hand linked"
        action = self.meeting.action_queue_ai_match()
        self.assertEqual(action['params']['type'], 'warning')
        self.assertFalse(self.meeting.ai_match_queued)

    def test_queue_cron_drains_and_clears_the_flag(self):
        self.meeting.sudo().with_context(sembly_sync=True).write(
            {'ai_match_queued': True})
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.9)):
            self.Meeting._cron_ai_match_queue()
        self.assertFalse(self.meeting.ai_match_queued)
        self.assertEqual(self.meeting.link_state, 'auto')
        self.assertFalse(self.Meeting.search_count(
            [('ai_match_queued', '=', True)]), "the queue must end empty")

    def test_queue_cron_skips_a_record_that_became_manual_meanwhile(self):
        """Queued, then hand-linked before the cron ran: the human wins."""
        self.meeting.sudo().with_context(sembly_sync=True).write(
            {'ai_match_queued': True})
        with patch.object(type(self.Meeting), '_sembly_link_fields',
                          return_value={'name'}):
            self.meeting.name = "Hand linked meanwhile"
        with self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id,
                                         confidence=0.99)) as ask:
            self.Meeting._cron_ai_match_queue()
        ask.assert_not_called()
        self.assertFalse(self.meeting.ai_match_queued,
                         "the stale request is dropped, not left to wedge the queue")
        self.assertEqual(self.meeting.link_state, 'manual')

    def test_a_crashing_match_does_not_wedge_the_queue(self):
        """The flag is cleared BEFORE matching, so one poisoned record costs
        its own request and nothing else."""
        second = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=fixtures.MEETING_ID + 51,
                 title="Behind the poisoned one"))
        (self.meeting | second).sudo().with_context(sembly_sync=True).write(
            {'ai_match_queued': True})
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("provider down")):
            self.Meeting._cron_ai_match_queue()   # must not raise
        self.assertFalse(self.Meeting.search_count(
            [('ai_match_queued', '=', True)]),
            "both requests consumed even though the agent was down")

    def test_agent_failure_does_not_kill_the_batch(self):
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("provider down")):
            self.meeting._ai_match()  # must not raise
        self.assertEqual(self.meeting.link_state, 'unlinked')
        self.assertTrue(self.env['sembly.sync.log'].search_count(
            [('channel', '=', 'ai'), ('state', '=', 'error')]))

    # ------------------------------------------------------------- resilience
    def test_a_provider_outage_aborts_the_batch(self):
        """Consecutive failures mean the provider is down, not that these
        meetings are unmatchable. Grinding on multiplies identical log rows and
        burns quota — 'Selected model is at capacity' did exactly that."""
        batch = self.meeting
        for i in range(6):
            batch |= self.Meeting._upsert_from_mcp(
                dict(fixtures.LIST_MEETINGS_META, id=640000 + i,
                     title="Outage %s" % i))
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.ai_failure_streak', '3')

        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("at capacity")) as ask:
            batch._ai_match()
        self.assertEqual(ask.call_count, 3,
                         "must stop after the streak, not try all seven")

    def test_every_attempt_is_stamped_even_when_it_fails(self):
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ask_agent',
                             side_effect=ValueError("at capacity")):
            self.meeting._ai_match()
        self.assertTrue(self.meeting.ai_last_attempt,
                        "an unstamped failure would sit at the queue head forever")

    def test_the_batch_cron_rotates_instead_of_regrinding_the_head(self):
        """The failure mode this fixes: the newest unlinked meetings were
        re-attempted every run while everything behind them starved."""
        tried = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=650001, title="Already tried"))
        untried = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=650002, title="Never tried"))
        # 'tried' is NEWER, so the old ordering would always pick it first.
        tried.sudo().with_context(sembly_sync=True).write({
            'started_at': '2026-08-10 10:00:00',
            'ai_last_attempt': '2026-08-10 09:00:00'})
        untried.sudo().with_context(sembly_sync=True).write({
            'started_at': '2026-08-01 10:00:00', 'ai_last_attempt': False})

        # setUp's meeting is untried too, and newer — stamp it so the only
        # untried record left is the one under test.
        self.meeting.sudo().with_context(sembly_sync=True).write(
            {'ai_last_attempt': '2026-08-10 08:00:00'})

        seen = []
        self.env['ir.config_parameter'].sudo().set_param('sembly.match_batch_size', '1')
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ai_match_one',
                             lambda meeting, threshold: seen.append(meeting.id)):
            self.Meeting._cron_ai_match_batch()
        self.assertIn(untried.id, seen)
        self.assertNotIn(tried.id, seen,
                         "the never-tried meeting must come first")

    # ------------------------------------------------------- internal meetings
    def _as_fallback_target(self):
        """Stand in for what a satellite contributes.

        company_id is the only many2one the base owns and it is REQUIRED, so it
        cannot play an empty link field — applying the fallback is therefore
        tested in era_sembly_meetings_crm, on the real lead_id. What is tested
        here is the part the base actually owns: the domain analysis."""
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.internal_fallback_ref', 'res.company,%s' % self.company.id)
        return patch.object(type(self.Meeting), '_sembly_link_field_by_model',
                            return_value={'res.company': 'company_id'})

    def test_nothing_is_evaluated_when_no_fallback_is_configured(self):
        """The feature must cost nothing on an instance that has not turned it
        on — not one LLM call."""
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.internal_fallback_ref', '')
        with self._with_pools([]), self._answer(self._reply()) as ask:
            self.meeting._ai_match()
        ask.assert_not_called()

    def test_an_external_domain_settles_it_without_asking_the_model(self):
        """The domain test is free and deterministic, and can only say NO —
        so one real external attendee ends it before any LLM call."""
        self.meeting.sudo().write({'participant_emails': "buyer@acme-industrial.com"})
        with self._as_fallback_target(), self._with_pools([]), \
                self._answer('{"internal": true, "reason": "looks internal"}') as ask:
            internal, why = self.meeting._looks_internal()
        ask.assert_not_called()
        self.assertFalse(internal)
        self.assertIn('acme-industrial.com', why)

    def test_the_sembly_account_domain_can_be_neutralised(self):
        """The Sembly workspace account attends EVERY meeting. On the live
        instance its domain appeared in 100% of the meetings carrying any
        address, against 50% for the company domain — so left in the test it
        marks every single meeting external and the fallback never fires."""
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.ignored_domains', 'letsw.com')
        self.meeting.sudo().write({
            'participant_emails': "staff@era-derived-test.sa",
            'owner_email': "y@letsw.com"})
        self.assertNotIn('letsw.com', self.meeting._external_attendee_domains())

    def test_a_neutral_domain_does_not_make_a_meeting_internal(self):
        """Neutral means neutral: it must not hide a REAL external attendee
        sitting in the same meeting."""
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.ignored_domains', 'letsw.com')
        self.meeting.sudo().write({
            'participant_emails': "y@letsw.com\nbuyer@acme-industrial.com"})
        self.assertEqual(self.meeting._external_attendee_domains(),
                         {'acme-industrial.com'})

    def test_neutral_domains_tolerate_an_at_prefix(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.ignored_domains', '@letsw.com, @vendor.example')
        self.assertEqual(self.Meeting._ignored_domains(),
                         {'letsw.com', 'vendor.example'})

    def test_free_providers_count_neither_way(self):
        """Our own staff use gmail too, so treating it as external would veto
        real internal meetings."""
        self.meeting.sudo().write({
            'participant_emails': "someone@gmail.com\nother@outlook.com"})
        with self._as_fallback_target():
            self.assertFalse(self.meeting._external_attendee_domains())

    def test_partner_without_email_does_not_break_domain_analysis(self):
        """Odoo returns False for an empty Char; str.join must not receive it."""
        partner = self.env['res.partner'].create({'name': "No Email Attendee"})
        self.meeting.sudo().write({'partner_ids': [(6, 0, [partner.id])]})
        self.assertFalse(self.meeting._external_attendee_domains())

    def test_a_meeting_something_else_claimed_is_left_alone(self):
        """The fallback is for the UNCLAIMED only."""
        with self._as_fallback_target(), self._with_pools([self._pool()]), \
                self._answer(self._reply(company_id=self.company.id, confidence=0.95)) as ask:
            self.meeting._ai_match()
        self.assertEqual(self.meeting.link_state, 'auto')
        self.assertEqual(ask.call_count, 1, "no second call for the internal test")

    def test_our_own_domain_is_derived_not_configured(self):
        """A hand-maintained list rots: a new company domain would start
        reading as an external customer."""
        self.env['res.users'].sudo().create({
            'name': "Derived Domain Tester",
            'login': 'tester@era-derived-test.sa',
        })
        self.assertIn('era-derived-test.sa', self.Meeting._internal_domains())

    def test_a_portal_user_domain_is_not_ours(self):
        """share=True means a customer, so their domain must stay external."""
        self.env['res.users'].sudo().create({
            'name': "Portal Customer",
            'login': 'buyer@customer-portal-test.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self.assertNotIn('customer-portal-test.com', self.Meeting._internal_domains())

    # ------------------------------------------------------------- re-search
    def _unlinked(self, sembly_id, tried_days_ago=None):
        meeting = self.Meeting._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=sembly_id))
        values = {'link_state': 'unlinked', 'ai_match_queued': False}
        if tried_days_ago is not None:
            values['ai_last_attempt'] = fields.Datetime.now() - timedelta(days=tried_days_ago)
        meeting.sudo().with_context(sembly_sync=True).write(values)
        return meeting

    def _batch_picks(self):
        seen = []
        with self._with_pools([self._pool()]), \
                patch.object(type(self.Meeting), '_ai_match_one',
                             lambda meeting, threshold: seen.append(meeting.id)):
            self.Meeting._cron_ai_match_batch()
        return seen

    def test_an_unlinked_meeting_is_re_searched_after_the_cooldown(self):
        """The answer genuinely can change: the opportunity a meeting belongs
        to is often created after the meeting itself."""
        self.env['ir.config_parameter'].sudo().set_param('sembly.rematch_after_days', '7')
        stale = self._unlinked(660001, tried_days_ago=30)
        self.assertIn(stale.id, self._batch_picks())

    def test_a_recently_tried_meeting_is_left_alone(self):
        """Without a cooldown this cron never goes quiet: it would recycle the
        same ~1 800 unlinked meetings every 15 minutes, paying an LLM round
        trip each time to re-derive the same answer from unchanged data."""
        self.env['ir.config_parameter'].sudo().set_param('sembly.rematch_after_days', '7')
        fresh = self._unlinked(660002, tried_days_ago=1)
        self.assertNotIn(fresh.id, self._batch_picks())

    def test_never_tried_meetings_still_come_first(self):
        self.env['ir.config_parameter'].sudo().set_param('sembly.rematch_after_days', '7')
        self.env['ir.config_parameter'].sudo().set_param('sembly.match_batch_size', '1')
        # setUp's meeting is unlinked and never tried as well, and shares the
        # fixture's started_at — so it ties on the ordering and wins or loses
        # by id. Stamp it out of the running.
        self.meeting.sudo().with_context(sembly_sync=True).write(
            {'ai_last_attempt': fields.Datetime.now()})
        self._unlinked(660003, tried_days_ago=30)
        virgin = self._unlinked(660004)
        self.assertEqual(self._batch_picks(), [virgin.id])

    def test_re_searching_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param('sembly.rematch_after_days', '-1')
        stale = self._unlinked(660005, tried_days_ago=365)
        self.assertNotIn(stale.id, self._batch_picks())

    def test_a_non_string_in_the_agent_reply_does_not_kill_the_match(self):
        """Observed on production 16 times in two days: get_direct_response
        returned a list carrying a bool, and "\\n".join raised
        "sequence item 0: expected str instance, bool found" — losing the whole
        match for that meeting."""
        agent = self.env['ai.agent'].sudo().browse(
            self.Meeting._icp_int('sembly.ai_agent_id', 1)).exists()
        if not agent:
            self.skipTest("no ai.agent on this database")
        with patch.object(type(agent), 'get_direct_response',
                          return_value=[True, '{"company_id": null}']):
            answer = self.Meeting._ask_agent("hello")
        self.assertIn('company_id', answer)

    def test_a_falsy_agent_reply_becomes_an_empty_string(self):
        agent = self.env['ai.agent'].sudo().browse(
            self.Meeting._icp_int('sembly.ai_agent_id', 1)).exists()
        if not agent:
            self.skipTest("no ai.agent on this database")
        with patch.object(type(agent), 'get_direct_response', return_value=False):
            self.assertEqual(self.Meeting._ask_agent("hello"), '')

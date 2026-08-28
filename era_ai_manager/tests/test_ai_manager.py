import json
import os
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_ai_manager.models.outreach import REPLY_PLAYS


class EraAiCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.param = cls.env["ir.config_parameter"].sudo()
        cls.param.set_param("era_ai_manager.autonomy_mode", "ramp")
        cls.param.set_param("era_ai_manager.ramp_end_date", "")
        # Hold the send window open: otherwise the whole suite passes or fails
        # depending on the wall-clock hour it runs at. The window has its own test.
        cls.param.set_param("era_ai_manager.send_hour_start", "0")
        cls.param.set_param("era_ai_manager.send_hour_end", "24")
        cls.Outreach = cls.env["era.ai.outreach"].sudo()
        cls.Watchlist = cls.env["era.ai.watchlist"].sudo()

    def _livechat_available(self):
        field = self.env["discuss.channel"]._fields.get("channel_type")
        values = [key for key, __ in field.selection] if field and isinstance(
            field.selection, list) else []
        return "livechat" in values

    def _partner(self, name="Acme", **values):
        base = {"name": name, "email": "%s@example.test" % name.lower(),
                "is_company": True}
        base.update(values)
        return self.env["res.partner"].sudo().create(base)

    def _draft(self, partner=None, play="winback", **values):
        base = {
            "subject": "Hello",
            "body_html": "<p>Hello there</p>",
            "play": play,
            "channel": "email",
            "lang": "en_US",
            "agent_name": "test",
        }
        if partner:
            base["partner_id"] = partner.id
        base.update(values)
        return self.Outreach.create(base)


@tagged("post_install", "-at_install")
class TestEraAiWatchlist(EraAiCommon):
    """Watchlists are the seam that makes this module business-agnostic."""

    def _watchlist(self, **values):
        base = {
            "name": "Quiet customers",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('is_company', '=', True)]",
            "partner_field": "id",
            "play": "winback",
            "intent": "Ask what got in the way.",
        }
        base.update(values)
        return self.Watchlist.create(base)

    def test_a_watchlist_finds_its_audience(self):
        partner = self._partner("Quietco")
        watchlist = self._watchlist()
        self.assertIn(partner, watchlist.matching_records())
        self.assertGreaterEqual(watchlist.match_count, 1)
        self.assertEqual(watchlist.partner_of(partner), partner)

    def test_a_watchlist_can_hang_off_any_model(self):
        """The whole point: one module, unrelated trades."""
        watchlist = self._watchlist(
            name="Internal users",
            model_id=self.env.ref("base.model_res_users").id,
            domain="[('share', '=', False)]",
            partner_field="partner_id",
        )
        self.assertEqual(watchlist.model_name, "res.users")
        record = watchlist.matching_records(limit=1)
        self.assertTrue(record)
        self.assertEqual(watchlist.partner_of(record), record.partner_id)

    def test_a_broken_domain_is_rejected_on_save_not_at_3am(self):
        with self.assertRaises(ValidationError):
            self._watchlist(domain="[('no_such_field', '=', 1)]")
        with self.assertRaises(ValidationError):
            self._watchlist(domain="this is not a domain")

    def test_an_unknown_partner_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._watchlist(partner_field="not_a_field")

    def test_one_contact_gets_only_the_most_urgent_play(self):
        """A customer who is both quiet and overdue hears about one of them."""
        partner = self._partner("Doubled")
        # approve_anyway, not approve: this test is about priority ordering, and
        # the blast-radius heuristic would otherwise make it depend on how many
        # contacts the test database happens to hold.
        self._watchlist(name="Urgent", play="broken", priority=10,
                        domain="[('id', '=', %s)]" % partner.id).action_approve_anyway()
        self._watchlist(name="Less urgent", play="winback", priority=90,
                        domain="[('id', '=', %s)]" % partner.id).action_approve_anyway()
        due = [d for d in self.Watchlist.due_records() if d["partner"] == partner]
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["watchlist"].play, "broken")


@tagged("post_install", "-at_install")
class TestEraAiOutreach(EraAiCommon):
    # ---- autonomy -----------------------------------------------------
    def test_ramp_mode_holds_drafts_for_approval(self):
        draft = self._draft(self._partner("Ramp"))
        draft.action_submit()
        self.assertEqual(draft.state, "pending")

    def test_full_mode_auto_approves(self):
        self.param.set_param("era_ai_manager.autonomy_mode", "full")
        draft = self._draft(self._partner("Full"))
        draft.action_submit()
        self.assertEqual(draft.state, "approved")

    def test_ramp_flips_to_full_on_its_end_date(self):
        self.param.set_param(
            "era_ai_manager.ramp_end_date",
            fields.Date.to_string(fields.Date.today() - timedelta(days=1)),
        )
        self.assertEqual(self.Outreach._autonomy_mode(), "full")

    def test_ramp_holds_until_its_end_date(self):
        self.param.set_param(
            "era_ai_manager.ramp_end_date",
            fields.Date.to_string(fields.Date.today() + timedelta(days=7)),
        )
        self.assertEqual(self.Outreach._autonomy_mode(), "ramp")

    # ---- guardrails ---------------------------------------------------
    def test_frequency_cap_blocks_a_second_marketing_touch(self):
        partner = self._partner("Capped")
        self._draft(partner, play="winback", state="sent",
                    sent_at=fields.Datetime.now() - timedelta(days=2))
        second = self._draft(partner, play="low_stock")
        second.action_submit()
        self.assertEqual(second.state, "blocked")
        self.assertIn("Frequency cap", second.block_reason)

    def test_frequency_cap_allows_a_touch_after_the_window(self):
        partner = self._partner("Capok")
        self._draft(partner, play="winback", state="sent",
                    sent_at=fields.Datetime.now() - timedelta(days=20))
        second = self._draft(partner, play="low_stock")
        second.action_submit()
        self.assertEqual(second.state, "pending")

    def test_same_play_is_deduplicated_even_outside_the_cap(self):
        partner = self._partner("Dedup")
        self._draft(partner, play="winback", state="sent",
                    sent_at=fields.Datetime.now() - timedelta(days=20))
        repeat = self._draft(partner, play="winback")
        repeat.action_submit()
        self.assertEqual(repeat.state, "blocked")
        self.assertIn("Duplicate", repeat.block_reason)

    def test_a_watchlist_cooldown_overrides_the_global_one(self):
        partner = self._partner("Cooldown")
        watchlist = self.Watchlist.create({
            "name": "Slow", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[]", "partner_field": "id", "play": "winback",
            "intent": "x", "cooldown_days": 365,
        })
        self._draft(partner, play="winback", state="sent",
                    sent_at=fields.Datetime.now() - timedelta(days=100))
        repeat = self._draft(partner, play="winback", watchlist_id=watchlist.id)
        repeat.action_submit()
        self.assertEqual(repeat.state, "blocked")
        self.assertIn("Duplicate", repeat.block_reason)

    def test_blacklisted_recipient_is_never_marketed_to(self):
        partner = self._partner("Optout")
        self.env["mail.blacklist"].sudo()._add(partner.email)
        draft = self._draft(partner)
        draft.action_submit()
        self.assertEqual(draft.state, "blocked")
        self.assertIn("blacklist", draft.block_reason.lower())

    def test_a_reply_ignores_the_marketing_guardrails(self):
        """Answering someone who wrote to us is never solicitation."""
        partner = self._partner("Replying")
        self.env["mail.blacklist"].sudo()._add(partner.email)
        self._draft(partner, play="winback", state="sent",
                    sent_at=fields.Datetime.now())
        reply = self._draft(partner, play="reply")
        self.assertFalse(reply._guardrail_failure())
        self.assertFalse(reply._is_marketing())

    def test_missing_recipient_is_blocked(self):
        draft = self._draft(play="winback", email_to=False)
        draft.action_submit()
        self.assertEqual(draft.state, "blocked")

    def test_guardrails_are_rechecked_at_approval_time(self):
        partner = self._partner("Recheck")
        draft = self._draft(partner)
        draft.action_submit()
        self.assertEqual(draft.state, "pending")
        self.env["mail.blacklist"].sudo()._add(partner.email)
        draft.action_approve()
        self.assertEqual(draft.state, "blocked")

    # ---- the send window defers, it does not discard -------------------
    def test_outside_the_send_window_defers_instead_of_discarding(self):
        """A night-time agent run must not lose its work."""
        draft = self._draft(self._partner("Window"))
        with patch.object(type(self.Outreach), "_within_send_window",
                          return_value=False):
            draft.action_submit()
            self.assertEqual(draft.state, "pending")
            draft.action_approve()
            draft._deliver()
            self.assertEqual(draft.state, "approved", "the draft was discarded")
            self.assertFalse(draft.sent_at)
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            draft._deliver()
        self.assertEqual(draft.state, "sent")

    def test_send_now_overrides_the_window_but_not_the_blacklist(self):
        forced = self._draft(self._partner("Forced"))
        with patch.object(type(self.Outreach), "_within_send_window",
                          return_value=False), \
             patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            forced.action_send()
        self.assertEqual(forced.state, "sent")

        blocked_partner = self._partner("Forceblocked")
        self.env["mail.blacklist"].sudo()._add(blocked_partner.email)
        blocked = self._draft(blocked_partner)
        blocked.action_send()
        self.assertEqual(blocked.state, "blocked")

    # ---- delivery ------------------------------------------------------
    def test_approved_email_is_sent_and_stamped(self):
        draft = self._draft(self._partner("Sendme"))
        draft.action_submit()
        draft.action_approve()
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            draft._deliver()
        self.assertEqual(draft.state, "sent")
        self.assertTrue(draft.sent_at)
        self.assertTrue(draft.mail_id)

    def test_a_reply_posts_into_whatever_thread_it_names(self):
        """Generic on purpose: any mail.thread record, not just a ticket."""
        partner = self._partner("Threaded")
        draft = self._draft(partner, play="reply", channel="thread_reply",
                            thread_model="res.partner", thread_id=partner.id)
        self.assertEqual(draft.thread_ref, partner.display_name)
        before = len(partner.message_ids)
        draft.action_submit()
        draft.action_approve()
        draft._deliver()
        self.assertEqual(draft.state, "sent")
        self.assertGreater(len(partner.message_ids), before)

    def test_a_reply_to_a_deleted_record_is_blocked_not_crashed(self):
        draft = self._draft(self._partner("Gone"), play="reply",
                            channel="thread_reply",
                            thread_model="res.partner", thread_id=99999999)
        draft.action_submit()
        draft.action_approve()
        draft._deliver()
        self.assertEqual(draft.state, "blocked")

    def test_rejected_draft_is_never_delivered(self):
        draft = self._draft(self._partner("Rejected"))
        draft.action_reject()
        draft._deliver()
        self.assertEqual(draft.state, "rejected")
        self.assertFalse(draft.sent_at)


@tagged("post_install", "-at_install")
class TestEraAiProfile(EraAiCommon):
    """The business study: evidence from Python, judgement from the AI."""

    def test_the_survey_works_with_no_ai_configured_at_all(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.action_survey()
        self.assertEqual(profile.state, "surveyed")
        self.assertTrue(profile.surveyed_at)
        evidence = json.loads(profile.evidence_json)
        for key in ("company", "installed_apps", "record_volumes", "tempo"):
            self.assertIn(key, evidence)
        self.assertIn("res.partner", evidence["record_volumes"])

    def test_the_survey_only_reports_models_that_exist_here(self):
        """It must run on any database, including one missing every app."""
        profile = self.env["era.ai.profile"].sudo().current()
        evidence = profile._collect_evidence()
        for model_name in evidence["record_volumes"]:
            self.assertIsNotNone(self.env.get(model_name), model_name)

    def test_the_summary_is_readable_without_opening_the_json(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.action_survey()
        self.assertIn("Company:", profile.evidence_summary)
        self.assertIn("Apps in use:", profile.evidence_summary)

    def test_applying_without_a_brief_refuses_rather_than_wiping_the_persona(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.persona_brief = False
        with self.assertRaises(UserError):
            profile.action_apply()

    def test_applying_rewrites_the_persona_and_creates_the_watchlists(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({
            "persona_brief": "You manage a bakery. Customers are wholesale cafes.",
            "proposed_watchlists": json.dumps([{
                "name": "Cafes that stopped ordering",
                "model": "res.partner",
                "domain": "[('is_company', '=', True)]",
                "partner_field": "id",
                "play": "winback",
                "priority": 20,
                "intent": "Ask what changed. Cite their last order.",
            }]),
        })
        profile.action_apply()
        persona = self.env.ref("era_ai_manager.persona_manager")
        self.assertIn("bakery", persona.instructions)
        self.assertEqual(profile.state, "applied")
        watchlist = self.Watchlist.search(
            [("name", "=", "Cafes that stopped ordering")])
        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist.play, "winback")
        self.assertEqual(watchlist.priority, 20)

    def test_applying_twice_updates_rather_than_duplicates(self):
        profile = self.env["era.ai.profile"].sudo().current()
        proposal = [{
            "name": "Repeat list", "model": "res.partner",
            "domain": "[('is_company', '=', True)]", "partner_field": "id",
            "play": "winback", "intent": "x",
        }]
        profile.write({"persona_brief": "brief",
                       "proposed_watchlists": json.dumps(proposal)})
        profile.action_apply()
        proposal[0]["play"] = "check_in"
        profile.proposed_watchlists = json.dumps(proposal)
        profile.action_apply()
        watchlist = self.Watchlist.search([("name", "=", "Repeat list")])
        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist.play, "check_in")

    def test_one_unusable_proposal_does_not_lose_the_good_ones(self):
        """The AI will occasionally propose a model this database lacks."""
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({
            "persona_brief": "brief",
            "proposed_watchlists": json.dumps([
                {"name": "Impossible", "model": "no.such.model", "domain": "[]",
                 "play": "x", "intent": "x"},
                {"name": "Broken domain", "model": "res.partner",
                 "domain": "[('nope', '=', 1)]", "partner_field": "id",
                 "play": "x", "intent": "x"},
                {"name": "Perfectly fine", "model": "res.partner",
                 "domain": "[('is_company', '=', True)]", "partner_field": "id",
                 "play": "winback", "intent": "x"},
            ]),
        })
        profile.action_apply()
        self.assertTrue(self.Watchlist.search([("name", "=", "Perfectly fine")]))
        self.assertFalse(self.Watchlist.search([("name", "=", "Impossible")]))
        self.assertFalse(self.Watchlist.search([("name", "=", "Broken domain")]))

    def test_malformed_proposal_json_is_reported_not_swallowed(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({"persona_brief": "brief",
                       "proposed_watchlists": "not json at all"})
        with self.assertRaises(UserError):
            profile.action_apply()


@tagged("post_install", "-at_install")
class TestEraAiWatchdog(EraAiCommon):
    def test_an_unconfigured_manager_is_reported_not_silent(self):
        """No watchlists looks exactly like a healthy system: silent."""
        self.Watchlist.search([]).unlink()
        keys = {k for k, _n, _s, _d
                in self.env["era.ai.watchdog.alert"]._run_checks()}
        self.assertIn("no_watchlists", keys)

    def test_the_watchdog_does_not_re_nag_about_a_known_issue(self):
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        Alert.search([]).unlink()
        self.Watchlist.search([]).unlink()
        with patch.object(type(Alert), "_notify_owner", return_value=None):
            Alert._cron_watchdog()
            first = Alert.search_count([("check_key", "=", "no_watchlists")])
            Alert._cron_watchdog()
            second = Alert.search_count([("check_key", "=", "no_watchlists")])
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)

    def test_an_alert_resolves_itself_once_fixed(self):
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        alert = Alert.create({"check_key": "gone_away",
                              "name": "Not true any more", "severity": "warning"})
        with patch.object(type(Alert), "_notify_owner", return_value=None):
            Alert._cron_watchdog()
        self.assertEqual(alert.state, "resolved")
        self.assertTrue(alert.resolved_at)

    def test_the_checks_run_on_a_database_missing_every_optional_app(self):
        """This module installs on Community; helpdesk may simply not exist."""
        self.assertIsInstance(
            self.env["era.ai.watchdog.alert"]._run_checks(), list)


@tagged("post_install", "-at_install")
class TestEraAiStaff(EraAiCommon):
    def test_the_manager_runs_as_a_real_user_never_superuser(self):
        user = self.env.ref("era_ai_manager.user_ai_manager")
        self.assertNotEqual(user.id, 1)
        self.assertTrue(user.active)

    def test_the_manager_can_read_and_write_but_not_delete(self):
        user = self.env.ref("era_ai_manager.user_ai_manager")
        tools = set(user.aidoo_tool_ids.mapped("name"))
        for tool in ("orm_search_read", "orm_read", "orm_call", "sql_select",
                     "orm_create", "orm_write", "orm_action", "model_introspect"):
            self.assertIn(tool, tools, "missing tool %s" % tool)
        self.assertEqual(user.aidoo_zero_trust_mode, "off")
        self.assertNotIn("orm_unlink", tools,
                         "the manager must not delete records")

    def test_the_shipped_brief_is_generic_and_says_so(self):
        """It must not assume a trade before discovery has run."""
        brief = self.env.ref("era_ai_manager.persona_manager").instructions
        self.assertIn("PLACEHOLDER", brief)
        self.assertIn("era.ai.outreach", brief)
        self.assertIn("era.ai.watchlist", brief)
        for specific in ("Fatoratec", "ZATCA", "zatca.subscriber"):
            self.assertNotIn(specific, brief,
                             "the shipped brief still assumes one business")

    def test_every_agent_is_wired_to_the_persona_and_ships_off(self):
        persona = self.env.ref("era_ai_manager.persona_manager")
        agents = self.env["aidoo.scheduled"].with_context(
            active_test=False).search([("persona_id", "=", persona.id)])
        self.assertEqual(len(agents), 6)
        user = self.env.ref("era_ai_manager.user_ai_manager")
        for agent in agents:
            self.assertFalse(agent.active, "%s must ship inactive" % agent.name)
            self.assertEqual(agent.user_id, user)
            self.assertTrue(agent.skill_ids, "%s has no playbook" % agent.name)

    def test_the_discovery_playbook_proposes_and_stops(self):
        """It must not configure itself without a human in between."""
        skill = self.env.ref("era_ai_manager.skill_discovery")
        for topic in ("era.ai.profile", "evidence_json", "persona_brief",
                      "proposed_watchlists", "search_count"):
            self.assertIn(topic, skill.instructions, topic)
        self.assertIn("لا تنشئ قوائم المراقبة بنفسك", skill.instructions)

    def test_the_followup_playbook_defers_to_the_watchlist(self):
        skill = self.env.ref("era_ai_manager.skill_followup")
        self.assertIn("era.ai.watchlist", skill.instructions)
        self.assertIn("ولا تستبدل به شيئاً من عندك", skill.instructions)

    def test_no_playbook_hardcodes_a_particular_business(self):
        for xmlid in ("skill_discovery", "skill_inbox", "skill_followup",
                      "skill_campaign", "skill_watchdog", "skill_weekly"):
            skill = self.env.ref("era_ai_manager.%s" % xmlid)
            for specific in ("Fatoratec", "ZATCA", "zatca.subscriber"):
                self.assertNotIn(specific, skill.instructions,
                                 "%s assumes one business" % xmlid)


@tagged("post_install", "-at_install")
class TestEraAiWatchlistApproval(EraAiCommon):
    """The layer that makes self-discovery safe.

    The playbook tells the agent to propose rather than create, but it holds
    orm_create and the model is writable — so the prompt cannot be the control.
    Nothing acts until a human approves it, and approval itself refuses an
    audience that is really the whole customer base.
    """

    def _watchlist(self, **values):
        base = {
            "name": "Proposed audience",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('is_company', '=', True)]",
            "partner_field": "id",
            "play": "winback",
            "intent": "Ask what changed.",
        }
        base.update(values)
        return self.Watchlist.create(base)

    def test_a_new_watchlist_is_inert_until_approved(self):
        partner = self._partner("Inert")
        watchlist = self._watchlist(domain="[('id', '=', %s)]" % partner.id)
        self.assertEqual(watchlist.state, "draft")
        self.assertFalse(
            [d for d in self.Watchlist.due_records() if d["partner"] == partner],
            "an unapproved watchlist was acted on",
        )
        watchlist.action_approve()
        self.assertEqual(watchlist.state, "approved")
        self.assertTrue(
            [d for d in self.Watchlist.due_records() if d["partner"] == partner]
        )

    def test_anything_the_agent_creates_directly_lands_inert(self):
        """It has the tools to create one; that must simply not matter."""
        watchlist = self.Watchlist.create({
            "name": "Snuck in", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[]", "partner_field": "id", "play": "blast",
            "intent": "everyone",
        })
        self.assertEqual(watchlist.state, "draft")
        self.assertNotIn(watchlist, self.Watchlist.search([("state", "=", "approved")]))

    def test_an_audience_over_the_absolute_limit_is_held(self):
        self.param.set_param("era_ai_manager.max_audience_absolute", "1")
        self.param.set_param("era_ai_manager.max_audience_percent", "100")
        self._partner("Crowd1")
        self._partner("Crowd2")
        watchlist = self._watchlist()
        watchlist.action_approve()
        self.assertEqual(watchlist.state, "draft")
        self.assertIn("over the limit", watchlist.blocked_reason)

    def test_an_audience_that_is_most_of_the_base_is_held(self):
        """A domain of [] runs perfectly and matches everyone."""
        self.param.set_param("era_ai_manager.max_audience_absolute", "100000")
        self.param.set_param("era_ai_manager.max_audience_percent", "50")
        watchlist = self._watchlist(name="Everyone", domain="[]")
        watchlist.action_approve()
        self.assertEqual(watchlist.state, "draft")
        self.assertIn("mailing list", watchlist.blocked_reason)

    def test_an_audience_matching_nobody_is_held(self):
        watchlist = self._watchlist(name="Nobody", domain="[('id', '=', 0)]")
        watchlist.action_approve()
        self.assertEqual(watchlist.state, "draft")
        self.assertIn("nobody", watchlist.blocked_reason.lower())

    def test_the_owner_can_override_the_limit_deliberately(self):
        self.param.set_param("era_ai_manager.max_audience_absolute", "1")
        self._partner("Over1")
        self._partner("Over2")
        watchlist = self._watchlist()
        watchlist.action_approve()
        self.assertEqual(watchlist.state, "draft")
        watchlist.action_approve_anyway()
        self.assertEqual(watchlist.state, "approved")
        self.assertFalse(watchlist.blocked_reason)

    def test_holding_an_approved_watchlist_stops_it_acting(self):
        partner = self._partner("Paused")
        watchlist = self._watchlist(domain="[('id', '=', %s)]" % partner.id)
        watchlist.action_approve()
        watchlist.action_reset_to_draft()
        self.assertFalse(
            [d for d in self.Watchlist.due_records() if d["partner"] == partner]
        )


@tagged("post_install", "-at_install")
class TestEraAiApplyReport(EraAiCommon):
    """Applying must never silently drop a proposal.

    Believing an audience is covered when it never applied is worse than an
    obvious failure, so every proposal's fate is written down.
    """

    def _apply(self, proposals):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({"persona_brief": "brief",
                       "proposed_watchlists": json.dumps(proposals)})
        profile.action_apply()
        return profile

    def test_every_proposal_is_accounted_for(self):
        partner = self._partner("Reported")
        profile = self._apply([
            {"name": "Good one", "model": "res.partner",
             "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
             "play": "winback", "intent": "x"},
            {"name": "Absent model", "model": "no.such.model", "domain": "[]",
             "play": "x", "intent": "x"},
            {"name": "Bad domain", "model": "res.partner",
             "domain": "[('nope', '=', 1)]", "partner_field": "id",
             "play": "x", "intent": "x"},
        ])
        report = profile.apply_report
        self.assertIn("Good one", report)
        self.assertIn("Absent model", report)
        self.assertIn("Bad domain", report)
        self.assertIn("watching", report)
        self.assertIn("could not set up", report)

    def test_an_oversized_proposal_is_held_and_said_so(self):
        self.param.set_param("era_ai_manager.max_audience_percent", "50")
        profile = self._apply([
            {"name": "Everyone", "model": "res.partner", "domain": "[]",
             "partner_field": "id", "play": "blast", "intent": "x"},
        ])
        self.assertIn("not using", profile.apply_report.lower())
        watchlist = self.Watchlist.search([("name", "=", "Everyone")])
        self.assertEqual(watchlist.state, "draft")

    def test_applying_approves_only_what_passed(self):
        partner = self._partner("Narrow")
        self._apply([
            {"name": "Narrow list", "model": "res.partner",
             "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
             "play": "winback", "intent": "x"},
        ])
        watchlist = self.Watchlist.search([("name", "=", "Narrow list")])
        self.assertEqual(watchlist.state, "approved")
        self.assertGreaterEqual(watchlist.match_count, 1)


@tagged("post_install", "-at_install")
class TestEraAiSurveyFindsTheBusiness(EraAiCommon):
    """The survey has to find the customer base, whatever model it lives in.

    A curated list of standard Odoo models cannot do that: a SaaS keeping its
    accounts in a model of its own would be described as a website because
    website.visitor had the loudest row count. These tests pin the two things
    that prevent it — sweeping custom models, and ranking by customer shape
    rather than by size.
    """

    def setUp(self):
        super().setUp()
        self.profile = self.env["era.ai.profile"].sudo().current()

    def test_the_sweep_finds_custom_models_carrying_data(self):
        """era.ai.watchlist is itself a custom model — it must show up."""
        self.Watchlist.create({
            "name": "Something", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[]", "partner_field": "id", "play": "x", "intent": "x",
        })
        found = {m["model"]: m for m in self.profile._discover_data_models()}
        self.assertIn("era.ai.watchlist", found)
        self.assertTrue(found["era.ai.watchlist"]["custom"])
        self.assertEqual(found["era.ai.watchlist"]["from_module"], "era_ai_manager")

    def test_custom_models_expose_their_fields_to_the_agent(self):
        """Field names are how the agent works out what 'in trouble' means."""
        self.Watchlist.create({
            "name": "Fielded", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[]", "partner_field": "id", "play": "x", "intent": "x",
        })
        found = {m["model"]: m for m in self.profile._discover_data_models()}
        fields = found["era.ai.watchlist"].get("fields") or []
        self.assertIn("play", fields)
        self.assertIn("priority", fields)

    def test_a_customer_shaped_model_is_recognised(self):
        """Contact link + email + a recency date is what a customer looks like."""
        signals = self.profile._customer_signals(self.env["res.partner"])
        self.assertTrue(signals["email_fields"])
        signals = self.profile._customer_signals(self.env["era.ai.outreach"])
        self.assertIn("partner_id", signals["partner_links"])
        self.assertTrue(signals["email_fields"])
        self.assertTrue(signals["recency_fields"])
        self.assertTrue(signals["looks_like_customers"])

    def test_plumbing_is_not_mistaken_for_customers(self):
        """A log table has neither a contact nor a reason to be written to."""
        signals = self.profile._customer_signals(
            self.env["era.ai.watchdog.alert"])
        self.assertFalse(signals["partner_links"])
        self.assertFalse(signals["looks_like_customers"])

    def test_a_log_shaped_name_is_demoted_even_when_it_has_a_contact(self):
        """The widened recency rule lets logs in; the name rule ranks them down.

        mail.message points at a partner and carries dates, so it now reads as
        customer-shaped — that is the price of not missing a clinic whose field
        is appointment_date. Its name is what keeps it below the real thing.
        """
        self.assertTrue(
            self.profile._customer_signals(
                self.env["mail.message"])["looks_like_plumbing"])
        self.assertFalse(
            self.profile._customer_signals(
                self.env["res.partner"])["looks_like_plumbing"])
        self.assertFalse(
            self.profile._customer_signals(
                self.env["era.ai.outreach"])["looks_like_plumbing"])

    def test_customer_shaped_models_outrank_bigger_plumbing(self):
        """The bug this prevents: a 24k-row redirect log burying the 30-row
        table that is actually the business."""
        self.Watchlist.create({
            "name": "Plumbing", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[]", "partner_field": "id", "play": "x", "intent": "x",
        })
        for i in range(3):
            self._draft(self._partner("Ranked%s" % i))
        found = self.profile._discover_data_models()
        order = [m["model"] for m in found]
        self.assertIn("era.ai.outreach", order)
        self.assertIn("era.ai.watchlist", order)
        self.assertLess(
            order.index("era.ai.outreach"), order.index("era.ai.watchlist"),
            "a customer-shaped model must rank above plumbing",
        )

    def test_the_summary_points_the_reader_at_the_customer_models(self):
        for i in range(2):
            self._draft(self._partner("Summarised%s" % i))
        self.profile.action_survey()
        self.assertIn("look like customer records", self.profile.evidence_summary)

    def test_technical_noise_is_excluded(self):
        found = {m["model"] for m in self.profile._discover_data_models()}
        for noisy in ("ir.model", "ir.ui.view", "res.lang", "res.currency"):
            self.assertNotIn(noisy, found, "%s is Odoo, not the business" % noisy)

    def test_the_discovery_playbook_explains_the_ranking(self):
        skill = self.env.ref("era_ai_manager.skill_discovery")
        for topic in ("data_models", "looks_like_customers", "custom",
                      "recency_fields"):
            self.assertIn(topic, skill.instructions, topic)
        self.assertIn("عدد الصفوف وحده", skill.instructions)


@tagged("post_install", "-at_install")
class TestEraAiCapabilities(EraAiCommon):
    """The manager must work with whatever this database happens to have.

    A brief that promises a channel the database does not have is worse than
    one that never mentions it, and an agent switched on for an absent app
    produces an empty run for ever.
    """

    def setUp(self):
        super().setUp()
        self.profile = self.env["era.ai.profile"].sudo().current()

    def test_capabilities_are_decided_by_models_not_module_names(self):
        found = {c["capability"]: c for c in self.profile._detect_capabilities()}
        # mail.activity ships with mail, which this module depends on.
        self.assertTrue(found["activities"]["available"])
        for entry in found.values():
            for model in entry["models"]:
                self.assertIsNotNone(self.env.get(model["model"]), model["model"])
            if not entry["available"]:
                self.assertFalse(entry["models"], entry["capability"])

    def test_email_campaigns_are_available_here(self):
        """mass_mailing is a dependency, so this must be detected."""
        found = {c["capability"]: c for c in self.profile._detect_capabilities()}
        self.assertTrue(found["email_campaigns"]["available"])
        self.assertEqual(found["email_campaigns"]["feeds_agent"], "campaign")

    def test_an_absent_app_is_reported_absent_not_omitted(self):
        """The agent has to be able to see that WhatsApp is NOT an option."""
        found = {c["capability"]: c for c in self.profile._detect_capabilities()}
        self.assertIn("whatsapp", found)
        if self.env.get("whatsapp.message") is None:
            self.assertFalse(found["whatsapp"]["available"])

    def test_the_summary_lists_what_can_and_cannot_be_used(self):
        self.profile.action_survey()
        summary = self.profile.evidence_summary
        self.assertIn("What the manager can work with here", summary)
        self.assertIn("Email marketing", summary)

    def test_only_agents_with_something_to_do_are_recommended(self):
        self.profile.action_survey()
        recommended = self.profile.recommended_agents or ""
        # These always have work: the watchdog and the weekly report.
        self.assertIn("watchdog", recommended)
        self.assertIn("weekly", recommended)
        # Campaigns are possible because mass_mailing is installed.
        self.assertIn("campaign", recommended)
        # Follow-up needs an approved audience, and there is none yet.
        self.assertNotIn("followup", recommended)

    def test_followup_is_recommended_once_an_audience_is_approved(self):
        partner = self._partner("Recommended")
        self.Watchlist.create({
            "name": "Someone", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
            "play": "winback", "intent": "x",
        }).action_approve_anyway()
        self.profile._compute_recommended_agents()
        self.assertIn("followup", self.profile.recommended_agents)

    def test_applying_retimes_the_campaign_agent_to_the_trade(self):
        """A cafe supplier earns weekly; a project firm does not."""
        self.profile.write({
            "persona_brief": "You supply cafes; they reorder every week.",
            "proposed_campaign_days": 7,
            "proposed_watchlists": "[]",
        })
        self.profile.action_apply()
        agent = self.env.ref("era_ai_manager.agent_campaign")
        self.assertEqual(agent.interval_number, 7)
        self.assertEqual(agent.interval_type, "days")
        self.assertIn("one campaign every 7 days", self.profile.apply_report)

    def test_an_absurd_cadence_is_clamped(self):
        self.profile.write({"persona_brief": "b", "proposed_campaign_days": 1,
                            "proposed_watchlists": "[]"})
        self.profile.action_apply()
        self.assertEqual(
            self.env.ref("era_ai_manager.agent_campaign").interval_number, 7)

    def test_the_discovery_playbook_reads_the_capability_map(self):
        skill = self.env.ref("era_ai_manager.skill_discovery")
        for topic in ("capabilities", "available = false",
                      "proposed_campaign_days", "ما أعمل به هنا"):
            self.assertIn(topic, skill.instructions, topic)

    def test_the_inbox_playbook_works_every_inbound_app_not_just_one(self):
        skill = self.env.ref("era_ai_manager.skill_inbox")
        self.assertIn("helpdesk.ticket", skill.instructions)
        self.assertIn("crm.lead", skill.instructions)
        self.assertIn("capabilities", skill.instructions)

    def test_the_campaign_playbook_stops_when_it_has_no_channel(self):
        skill = self.env.ref("era_ai_manager.skill_campaign")
        self.assertIn("التسويق بالبريد متاح", skill.instructions)


@tagged("post_install", "-at_install")
class TestEraAiSettings(EraAiCommon):
    """Rendering the settings view is not the same as opening the page.

    get_views() only compiles the arch. The crash that took the whole Settings
    page down came from default_get -> _get_classified_fields, which only runs
    when a settings record is actually created — so that is what these tests do.
    """

    def test_the_settings_page_can_actually_be_opened(self):
        """Odoo refuses a Date on a config_parameter field, and the exception
        is raised while classifying EVERY field — so one bad field breaks
        Settings entirely, not just this module's block."""
        settings = self.env["res.config.settings"].create({})
        self.assertTrue(settings)

    def test_every_config_parameter_field_has_a_type_odoo_accepts(self):
        classified = self.env["res.config.settings"]._get_classified_fields(None)
        allowed = ("boolean", "integer", "float", "char", "selection",
                   "many2one", "datetime")
        ours = [name for name, __ in classified["config"]
                if name.startswith("era_ai_")]
        self.assertTrue(ours, "no era_ai_ config fields were classified at all")
        for name in ours:
            self.assertIn(self.env["res.config.settings"]._fields[name].type,
                          allowed, name)

    def test_the_ramp_date_survives_a_full_round_trip(self):
        """It is not a config_parameter field any more, so its read and write
        are hand-written and need covering."""
        target = fields.Date.today() + timedelta(days=21)
        settings = self.env["res.config.settings"].create(
            {"era_ai_ramp_end_date": target})
        settings.execute()
        self.assertEqual(
            self.param.get_param("era_ai_manager.ramp_end_date"),
            fields.Date.to_string(target),
        )
        reopened = self.env["res.config.settings"].create({})
        self.assertEqual(reopened.era_ai_ramp_end_date, target)

    def test_clearing_the_ramp_date_clears_the_parameter(self):
        self.param.set_param("era_ai_manager.ramp_end_date",
                             fields.Date.to_string(fields.Date.today()))
        settings = self.env["res.config.settings"].create(
            {"era_ai_ramp_end_date": False})
        settings.execute()
        self.assertFalse(
            self.param.get_param("era_ai_manager.ramp_end_date"))

    def test_the_other_settings_round_trip_too(self):
        settings = self.env["res.config.settings"].create({
            "era_ai_cap_days": 14,
            "era_ai_mail_from": "hello@example.test",
            "era_ai_autonomy_mode": "full",
        })
        settings.execute()
        self.assertEqual(self.param.get_param("era_ai_manager.cap_days"), "14")
        self.assertEqual(self.param.get_param("era_ai_manager.mail_from"),
                         "hello@example.test")
        self.assertEqual(self.param.get_param("era_ai_manager.autonomy_mode"),
                         "full")


@tagged("post_install", "-at_install")
class TestEraAiAgentPermissions(EraAiCommon):
    """Everything here runs AS the agent user, never sudo.

    The whole module shipped read-only for the AI and nobody noticed, because
    every other test in this file uses .sudo(). The agent ran, wrote a brief,
    had nowhere to put it, and reported "my role still lacks write access" into
    a chat log no one was reading. These tests are the ones that would have
    caught it, so they are deliberately written from the agent's side of the
    permission wall.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent_user = cls.env.ref("era_ai_manager.user_ai_manager")
        cls.as_agent = cls.env(user=cls.agent_user)

    def test_the_agent_can_create_the_outreach_drafts_that_are_its_job(self):
        partner = self._partner("AgentDraft")
        draft = self.as_agent["era.ai.outreach"].create({
            "subject": "Hello", "body_html": "<p>Hi</p>", "play": "winback",
            "channel": "email", "lang": "en_US", "agent_name": "followup",
            "partner_id": partner.id,
        })
        self.assertTrue(draft)
        draft.write({"rationale": "because they went quiet"})

    def test_the_agent_can_write_its_findings_onto_the_profile(self):
        """The exact write that failed in production."""
        profile = self.env["era.ai.profile"].sudo().current()
        profile.with_user(self.agent_user).write({
            "business_summary": "A wholesale bakery.",
            "persona_brief": "You manage a wholesale bakery.",
            "proposed_watchlists": "[]",
            "proposed_campaign_days": 7,
        })
        self.assertEqual(profile.business_summary, "A wholesale bakery.")

    def test_the_agent_can_propose_a_watchlist(self):
        watchlist = self.as_agent["era.ai.watchlist"].create({
            "name": "Proposed by the agent",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('is_company', '=', True)]",
            "partner_field": "id", "play": "winback", "intent": "Ask why.",
        })
        self.assertEqual(watchlist.state, "draft")

    def test_the_agent_cannot_approve_its_own_proposal(self):
        """It has write rights, so the gate cannot live in the prompt."""
        watchlist = self.as_agent["era.ai.watchlist"].create({
            "name": "Self approved?",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('is_company', '=', True)]",
            "partner_field": "id", "play": "winback", "intent": "x",
            "state": "approved",
        })
        self.assertEqual(watchlist.state, "draft", "the agent approved itself")
        watchlist.with_user(self.agent_user).write({"state": "approved"})
        self.assertEqual(watchlist.state, "draft", "the agent approved itself")

    def test_the_owner_can_still_approve(self):
        watchlist = self.Watchlist.create({
            "name": "Owner approved",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % self._partner("Approvable").id,
            "partner_field": "id", "play": "winback", "intent": "x",
        })
        watchlist.action_approve_anyway()
        self.assertEqual(watchlist.state, "approved")

    def test_the_agent_cannot_rewrite_the_evidence_it_was_given(self):
        """The brief promises the survey is Python's; keep that true."""
        profile = self.env["era.ai.profile"].sudo().current()
        profile.action_survey()
        real = profile.evidence_json
        profile.with_user(self.agent_user).write({
            "evidence_json": '{"company": {"name": "Something else"}}',
            "business_summary": "still allowed",
        })
        self.assertEqual(profile.evidence_json, real, "evidence was tampered with")
        self.assertEqual(profile.business_summary, "still allowed")

    def test_the_agent_cannot_delete_anything(self):
        from odoo.exceptions import AccessError

        partner = self._partner("Undeletable")
        draft = self.as_agent["era.ai.outreach"].create({
            "subject": "x", "body_html": "<p>x</p>", "play": "winback",
            "channel": "email", "lang": "en_US", "partner_id": partner.id,
        })
        with self.assertRaises(AccessError):
            draft.unlink()

    def test_the_agent_can_read_the_watchdog_but_not_silence_it(self):
        from odoo.exceptions import AccessError

        alert = self.env["era.ai.watchdog.alert"].sudo().create({
            "check_key": "k", "name": "n", "severity": "warning",
        })
        alert.with_user(self.agent_user).read(["name"])
        with self.assertRaises(AccessError):
            alert.with_user(self.agent_user).write({"state": "resolved"})

    def test_every_model_the_agent_needs_is_reachable_as_that_user(self):
        """A blunt end-to-end check of the permission wall."""
        expected = {
            "era.ai.outreach": ("read", "write", "create"),
            "era.ai.watchlist": ("read", "write", "create"),
            "era.ai.profile": ("read", "write"),
            "era.ai.watchdog.alert": ("read",),
        }
        for model, operations in expected.items():
            for operation in operations:
                self.assertTrue(
                    self.as_agent[model].check_access_rights(
                        operation, raise_exception=False),
                    "the agent cannot %s %s" % (operation, model),
                )


@tagged("post_install", "-at_install")
class TestEraAiRelativeDates(EraAiCommon):
    """A literal date in a watchlist is a bug with a delay on it.

    The domain is stored as data and read with literal_eval, so it cannot
    compute. Left as-is, the AI writes today's cutoff into the domain and the
    audience quietly drains away over the following weeks.
    """

    def _watchlist(self, domain):
        return self.Watchlist.create({
            "name": "Timed",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": domain, "partner_field": "id",
            "play": "winback", "intent": "x",
        })

    def test_days_ago_resolves_to_a_moving_cutoff(self):
        watchlist = self._watchlist(
            "[('create_date', '<', '{{days_ago:30}}')]")
        resolved = watchlist._domain()
        cutoff = fields.Datetime.to_datetime(resolved[0][2])
        self.assertIsNotNone(cutoff)
        age = (fields.Datetime.now() - cutoff).days
        self.assertEqual(age, 30)

    def test_days_ahead_and_hours_ago_resolve_too(self):
        ahead = self._watchlist(
            "[('create_date', '<', '{{days_ahead:7}}')]")._domain()[0][2]
        self.assertGreater(fields.Datetime.to_datetime(ahead),
                           fields.Datetime.now())
        hours = self._watchlist(
            "[('create_date', '<', '{{hours_ago:6}}')]")._domain()[0][2]
        self.assertLess(fields.Datetime.to_datetime(hours),
                        fields.Datetime.now())

    def test_a_relative_domain_actually_selects_records(self):
        partner = self._partner("Timeboxed")
        watchlist = self._watchlist(
            "[('id', '=', %s), ('create_date', '<', '{{days_ahead:1}}')]"
            % partner.id)
        self.assertIn(partner, watchlist.matching_records())
        self.assertEqual(watchlist.match_count, 1)

    def test_a_relative_domain_passes_validation_on_save(self):
        """The constraint must resolve the token before testing the domain,
        or every relative watchlist would be rejected as a bad date."""
        watchlist = self._watchlist(
            "[('create_date', '<', '{{days_ago:5}}')]")
        self.assertTrue(watchlist)

    def test_ordinary_values_are_left_alone(self):
        watchlist = self._watchlist("[('name', '=', 'Untouched')]")
        self.assertEqual(watchlist._domain(), [("name", "=", "Untouched")])

    def test_a_malformed_token_is_treated_as_a_plain_string(self):
        """Better a domain that matches nothing than one that crashes a cron."""
        watchlist = self._watchlist("[('name', '=', '{{days_ago:}}')]")
        self.assertEqual(watchlist._domain()[0][2], "{{days_ago:}}")

    def test_the_playbook_tells_the_agent_not_to_hardcode_dates(self):
        skill = self.env.ref("era_ai_manager.skill_discovery")
        self.assertIn("لا تضع تاريخاً ثابتاً في نطاق أبداً", skill.instructions)
        self.assertIn("{{days_ago:N}}", skill.instructions)


@tagged("post_install", "-at_install")
class TestEraAiLanguage(EraAiCommon):
    """An Arabic business should not be handed an English report about itself.

    The first real study wrote the owner's own brief in English, because the
    evidence listed the installed languages and never said which one belongs
    to the person reading it, and the persona's only language rule was about
    customer messages.
    """

    def setUp(self):
        super().setUp()
        self.profile = self.env["era.ai.profile"].sudo().current()

    def test_the_survey_states_which_language_the_owner_reads(self):
        self.profile.action_survey()
        evidence = json.loads(self.profile.evidence_json)
        self.assertIn("owner_language", evidence)
        self.assertTrue(evidence["owner_language"])
        self.assertIn("the owner reads", self.profile.evidence_summary)

    def test_the_owner_email_decides_the_language(self):
        owner = self.env["res.users"].sudo().create({
            "name": "Owner", "login": "owner@example.test",
            "email": "owner@example.test", "lang": "en_US",
        })
        self.param.set_param("era_ai_manager.owner_email", owner.email)
        self.assertEqual(self.profile._owner_language(), "en_US")

    def test_it_falls_back_rather_than_guessing_nothing(self):
        self.param.set_param("era_ai_manager.owner_email", "nobody@example.test")
        self.assertTrue(self.profile._owner_language())

    def test_the_persona_separates_the_three_readers(self):
        brief = self.env.ref("era_ai_manager.persona_manager").instructions
        self.assertIn("إلى العميل", brief)
        self.assertIn("إلى المالك", brief)
        self.assertIn("إلى الآلة", brief)
        self.assertIn("owner_language", brief)

    def test_the_owner_facing_playbooks_say_which_language(self):
        for xmlid in ("skill_discovery", "skill_weekly", "skill_watchdog"):
            skill = self.env.ref("era_ai_manager.%s" % xmlid)
            self.assertIn("owner_language", skill.instructions, xmlid)

    def test_identifiers_are_never_translated(self):
        """Translating a field name would break the query it appears in."""
        brief = self.env.ref("era_ai_manager.persona_manager").instructions
        self.assertIn("مُعرِّفات لا نصوص", brief)
        discovery = self.env.ref("era_ai_manager.skill_discovery").instructions
        self.assertIn("مُعرِّفات لا نصوص", discovery)

    def test_the_arabic_playbooks_keep_their_identifiers_in_english(self):
        """Translating a model or field name would break every query it is in.

        The playbooks ship in Arabic because aidoo's instructions field is not
        translatable, so the shipped text is the only text. That makes it easy
        to translate an identifier by accident, which is why this is asserted
        rather than trusted.
        """
        identifiers = {
            "skill_discovery": ["era.ai.profile", "evidence_json",
                                "proposed_watchlists", "search_count",
                                "{{days_ago:N}}", "looks_like_customers",
                                "owner_language", "partner_field"],
            "skill_inbox": ["helpdesk.ticket", "crm.lead", "era.ai.outreach",
                            "thread_model", "'reply'"],
            "skill_followup": ["era.ai.watchlist", "cooldown_days",
                               "watchlist_id", "era.ai.outreach"],
            "skill_campaign": ["mailing.list", "mailing.contact",
                               "mail.blacklist", "'newsletter'"],
            "skill_watchdog": ["era.ai.watchdog.alert", "owner_language"],
            "skill_weekly": ["owner_language", "era.ai.profile"],
        }
        for xmlid, names in identifiers.items():
            instructions = self.env.ref(
                "era_ai_manager.%s" % xmlid).instructions
            for name in names:
                self.assertIn(name, instructions,
                              "%s lost the identifier %s" % (xmlid, name))

    def test_the_shipped_playbooks_are_actually_in_arabic(self):
        import re

        arabic = re.compile(r"[\u0600-\u06FF]")
        for xmlid in ("skill_discovery", "skill_inbox", "skill_followup",
                      "skill_campaign", "skill_watchdog", "skill_weekly"):
            text = self.env.ref("era_ai_manager.%s" % xmlid).instructions
            share = len(arabic.findall(text)) / max(1, len(text))
            self.assertGreater(share, 0.4,
                               "%s does not read as Arabic" % xmlid)
        persona = self.env.ref("era_ai_manager.persona_manager").instructions
        self.assertGreater(len(arabic.findall(persona)) / len(persona), 0.4)

    def test_playbooks_ship_updatable_so_a_fix_can_reach_an_install(self):
        """The persona is the owner's and must survive upgrades; the playbooks
        are ours and must not be frozen by the same rule."""
        for xmlid in ("skill_discovery", "skill_weekly"):
            record = self.env["ir.model.data"].sudo().search([
                ("module", "=", "era_ai_manager"), ("name", "=", xmlid),
            ])
            self.assertTrue(record)
            self.assertFalse(record.noupdate, "%s is frozen" % xmlid)
        persona = self.env["ir.model.data"].sudo().search([
            ("module", "=", "era_ai_manager"), ("name", "=", "persona_manager"),
        ])
        self.assertTrue(persona.noupdate, "an upgrade would overwrite the brief")


@tagged("post_install", "-at_install")
class TestEraAiRestudy(EraAiCommon):
    """There has to be a way to say "do it again".

    Discovery is told to stop when a complete proposal already sits on the
    record, so it does not rewrite a good brief every month. The cost is that
    a brief which is itself wrong — written before a fix, in the wrong
    language, or simply poor — would stand for ever, because the agent decides
    by looking at exactly the fields that are wrong.
    """

    def setUp(self):
        super().setUp()
        self.profile = self.env["era.ai.profile"].sudo().current()
        self.profile.write({
            "business_summary": "An English summary written before the fix.",
            "persona_brief": "An English brief.",
            "proposed_watchlists": "[]",
            "apply_report": "old report",
            "state": "applied",
        })

    def test_restudy_clears_what_the_agent_looks_at(self):
        with patch.object(type(self.profile), "action_study_with_ai",
                          return_value=True):
            self.profile.action_restudy()
        self.assertFalse(self.profile.business_summary)
        self.assertFalse(self.profile.persona_brief)
        self.assertFalse(self.profile.proposed_watchlists)
        self.assertFalse(self.profile.apply_report)

    def test_restudy_takes_a_fresh_survey_first(self):
        """A new study must not reason from a stale snapshot."""
        self.profile.write({"evidence_json": False, "surveyed_at": False})
        with patch.object(type(self.profile), "action_study_with_ai",
                          return_value=True):
            self.profile.action_restudy()
        self.assertTrue(self.profile.evidence_json)
        self.assertTrue(self.profile.surveyed_at)
        self.assertEqual(self.profile.state, "surveyed")

    def test_restudy_does_not_touch_the_live_persona(self):
        """Only Apply changes what the manager actually works from."""
        persona = self.env.ref("era_ai_manager.persona_manager")
        before = persona.instructions
        with patch.object(type(self.profile), "action_study_with_ai",
                          return_value=True):
            self.profile.action_restudy()
        self.assertEqual(persona.instructions, before)

    def test_restudy_does_not_touch_approved_watchlists(self):
        """A re-study proposes again; it does not disarm what is already live."""
        partner = self._partner("Kept")
        watchlist = self.Watchlist.create({
            "name": "Live one",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % partner.id,
            "partner_field": "id", "play": "winback", "intent": "x",
        })
        watchlist.action_approve_anyway()
        with patch.object(type(self.profile), "action_study_with_ai",
                          return_value=True):
            self.profile.action_restudy()
        self.assertEqual(watchlist.state, "approved")

    def test_the_discovery_prompt_still_guards_against_pointless_reruns(self):
        agent = self.env.ref("era_ai_manager.agent_discovery")
        self.assertIn("فارغة", agent.prompt)
        self.assertIn("ولا تقل إنه لا جديد وتتوقف إلا", agent.prompt)


@tagged("post_install", "-at_install")
class TestEraAiOneStudyAtATime(EraAiCommon):
    """Never start a study over a study.

    aidoo already refuses to stack a run onto a running session — it silently
    defers. Silently is the problem: this module went on telling the owner
    "the agent is reading your database, refresh in a minute" while nothing had
    been launched at all, which turns a quiet no-op into a lie.
    """

    def setUp(self):
        super().setUp()
        self.profile = self.env["era.ai.profile"].sudo().current()
        self.agent = self.env.ref("era_ai_manager.agent_discovery")

    def _running_session(self, pid=False):
        return self.env["aidoo.session"].sudo().create({
            "name": "study", "provider": "claude",
            "user_id": self.env.ref("era_ai_manager.user_ai_manager").id,
            "scheduled_id": self.agent.id,
            "state": "running", "last_run_pid": pid,
        })

    def test_a_second_study_is_refused_while_one_is_alive(self):
        self._running_session(pid=os.getpid())  # a pid that really exists
        with self.assertRaises(UserError) as caught:
            self.profile.action_study_with_ai()
        self.assertIn("Wait for it to finish", str(caught.exception))

    def test_a_dead_study_says_so_instead_of_telling_you_to_wait(self):
        """Waiting for a process that no longer exists is the worst advice."""
        self._running_session(pid=0)
        with self.assertRaises(UserError) as caught:
            self.profile.action_study_with_ai()
        message = str(caught.exception)
        self.assertIn("its process is", message)
        self.assertIn("Stop the study", message)

    def test_restudy_is_refused_too(self):
        """It is the same launch underneath, and it also deletes the brief."""
        self.profile.write({"persona_brief": "existing"})
        self._running_session(pid=os.getpid())
        with self.assertRaises(UserError):
            self.profile.action_restudy()
        self.assertEqual(self.profile.persona_brief, "existing",
                         "a refused restudy still destroyed the brief")

    def test_stopping_releases_the_lock(self):
        session = self._running_session(pid=0)
        self.profile.action_stop_study()
        self.assertNotEqual(session.state, "running")
        self.assertFalse(self.profile._running_study())

    def test_stopping_nothing_says_so(self):
        with self.assertRaises(UserError):
            self.profile.action_stop_study()

    def test_a_finished_study_does_not_block_the_next(self):
        session = self._running_session(pid=0)
        session.state = "done"
        self.assertFalse(self.profile._running_study())


@tagged("post_install", "-at_install")
class TestEraAiOwnerLanguageInEmails(EraAiCommon):
    """Everything the owner reads must render in the owner's language.

    These crons run as OdooBot, so every _() in them resolved against
    OdooBot's English while the owner reads Arabic. The Arabic translations
    existed the whole time and were simply never selected — which is the worst
    kind of bug, because it looks like a missing translation.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"].sudo()._activate_lang("ar_001")
        owner = cls.env["res.users"].sudo().create({
            "name": "Owner", "login": "arabic.owner@example.test",
            "email": "arabic.owner@example.test", "lang": "ar_001",
        })
        cls.param.set_param("era_ai_manager.owner_email", owner.email)

    def test_the_owner_language_is_what_drives_it(self):
        self.assertEqual(
            self.env["era.ai.profile"].owner_language(), "ar_001")

    def _lang_at_creation(self, model_name, run):
        """The language a record was actually rendered in.

        Re-reading the record afterwards is useless: a search returns it in
        the searching environment, so the language it was WRITTEN in is gone.
        The only place that truth exists is the create call itself.
        """
        seen = {}
        model = type(self.env[model_name].sudo())
        original = model.create

        def spy(records, vals_list):
            seen.setdefault("lang", records.env.context.get("lang"))
            return original(records, vals_list)

        with patch.object(model, "create", spy), \
             patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            run()
        return seen.get("lang")

    def test_the_approval_digest_is_built_in_the_owners_language(self):
        partner = self._partner("Digested")
        self._draft(partner).write({"state": "pending"})
        as_cron = self.env["era.ai.outreach"].with_user(
            self.env.ref("base.user_root")).sudo()
        lang = self._lang_at_creation("mail.mail", as_cron._cron_pending_digest)
        self.assertEqual(lang, "ar_001",
                         "the digest was written in the cron's language")

    def test_the_watchdog_email_is_built_in_the_owners_language(self):
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        Alert.search([]).unlink()
        self.Watchlist.search([]).unlink()   # guarantees a finding
        as_cron = Alert.with_user(self.env.ref("base.user_root")).sudo()
        lang = self._lang_at_creation("mail.mail", as_cron._cron_watchdog)
        self.assertEqual(lang, "ar_001")

    def test_the_stored_alert_text_is_in_the_owners_language_too(self):
        """Half-translating it would leave the list view English."""
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        Alert.search([]).unlink()
        self.Watchlist.search([]).unlink()
        as_cron = Alert.with_user(self.env.ref("base.user_root")).sudo()
        lang = self._lang_at_creation(
            "era.ai.watchdog.alert", as_cron._cron_watchdog)
        self.assertEqual(lang, "ar_001")

    def test_block_reasons_are_written_in_the_owners_language(self):
        """The owner has to read these to act on them."""
        partner = self._partner("Blocked")
        self.env["mail.blacklist"].sudo()._add(partner.email)
        self._draft(partner)
        queue = self.env["era.ai.outreach"].with_user(
            self.env.ref("base.user_root")).sudo()
        queue._cron_process_queue()
        draft = self.env["era.ai.outreach"].sudo().search(
            [("partner_id", "=", partner.id)], limit=1)
        self.assertEqual(draft.state, "blocked")
        self.assertTrue(draft.block_reason)

    def test_a_customer_message_still_follows_the_customer_not_the_owner(self):
        """The owner's language must not leak into customer-facing content."""
        partner = self._partner("Customer")
        draft = self._draft(partner, lang="en_US")
        self.assertEqual(draft.lang, "en_US")


@tagged("post_install", "-at_install")
class TestEraAiApplyReportReadable(EraAiCommon):
    """The report is read by a person deciding what to do next.

    "APPROVED  X — 2 matching now" says almost nothing: not two of what, not
    who they are, and not whether anything is about to be sent to them.
    """

    def _apply(self, proposals):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({"persona_brief": "brief",
                       "proposed_watchlists": json.dumps(proposals)})
        profile.action_apply()
        return profile

    def test_an_approved_audience_links_to_the_people_in_it(self):
        partner = self._partner("Linked")
        profile = self._apply([{
            "name": "Quiet ones", "model": "res.partner",
            "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
            "play": "winback", "intent": "x",
        }])
        watchlist = self.Watchlist.search([("name", "=", "Quiet ones")])
        self.assertIn("/odoo/era.ai.watchlist/%s" % watchlist.id,
                      profile.apply_report)
        self.assertIn("open it to see them", profile.apply_report)
        self.assertEqual(profile.apply_report.count(
            "/odoo/era.ai.watchlist/%s" % watchlist.id), 1,
            "two links to the same page is noise, not choice")

    def test_it_says_how_many_customers_not_just_a_number(self):
        partner = self._partner("Counted")
        profile = self._apply([{
            "name": "Counted list", "model": "res.partner",
            "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
            "play": "winback", "intent": "x",
        }])
        self.assertIn("customer", profile.apply_report)
        self.assertIn("watching", profile.apply_report)

    def test_a_held_audience_says_nobody_will_be_contacted(self):
        """The reader's real question is "is it about to email people?"."""
        self.param.set_param("era_ai_manager.max_audience_percent", "50")
        profile = self._apply([{
            "name": "Everyone", "model": "res.partner", "domain": "[]",
            "partner_field": "id", "play": "blast", "intent": "x",
        }])
        report = profile.apply_report
        self.assertIn("Nobody will be contacted", report)
        self.assertIn("approve it", report)

    def test_it_ends_by_saying_nothing_is_sent_without_approval(self):
        partner = self._partner("Reassured")
        profile = self._apply([{
            "name": "Reassuring", "model": "res.partner",
            "domain": "[('id', '=', %s)]" % partner.id, "partner_field": "id",
            "play": "winback", "intent": "x",
        }])
        self.assertIn("Nothing reaches a customer", profile.apply_report)
        self.assertIn("/odoo/action-", profile.apply_report)

    def test_the_cadence_line_explains_itself(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({"persona_brief": "b", "proposed_campaign_days": 14,
                       "proposed_watchlists": "[]"})
        profile.action_apply()
        self.assertIn("one campaign every 14 days", profile.apply_report)
        self.assertIn("you approve it", profile.apply_report)

    def test_nothing_proposed_says_so_plainly(self):
        profile = self.env["era.ai.profile"].sudo().current()
        profile.write({"persona_brief": "b", "proposed_watchlists": "[]"})
        with patch.object(type(profile), "_apply_cadence", return_value=[]):
            profile.action_apply()
        self.assertIn("nothing changed", profile.apply_report)

    def test_the_report_is_html_so_the_links_are_clickable(self):
        self.assertEqual(
            self.env["era.ai.profile"]._fields["apply_report"].type, "html")


@tagged("post_install", "-at_install")
class TestEraAiOwnerEmailsAreReadable(EraAiCommon):
    """The emails arrive when the owner is not at their desk.

    They were a bare table with a jargon column called "Play" and no links:
    nothing to act on without first going and finding the records by hand.
    """

    def setUpEmail(self):
        self.param.set_param("era_ai_manager.owner_email", "owner@example.test")

    def _sent_mail(self, run):
        self.setUpEmail()
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            run()
        return self.env["mail.mail"].sudo().search(
            [("email_to", "=", "owner@example.test")], order="id desc", limit=1)

    # ---- the approval digest ------------------------------------------
    def test_the_digest_names_the_customer_and_links_the_draft(self):
        partner = self._partner("Bakery Co")
        draft = self._draft(partner, subject="Your points are running low")
        draft.write({"state": "pending"})
        mail = self._sent_mail(self.Outreach._cron_pending_digest)
        self.assertTrue(mail, "no digest was sent")
        self.assertIn("Bakery Co", mail.body_html)
        self.assertIn("Your points are running low", mail.body_html)
        self.assertIn("/odoo/era.ai.outreach/%s" % draft.id, mail.body_html)

    def test_the_digest_says_nothing_has_been_sent(self):
        """The reader's first question at 7am is whether mail already went out."""
        partner = self._partner("Anxious")
        self._draft(partner).write({"state": "pending"})
        mail = self._sent_mail(self.Outreach._cron_pending_digest)
        self.assertIn("None of them has been sent", mail.body_html)
        self.assertIn("If you do nothing", mail.body_html)

    def test_the_digest_offers_one_place_to_act(self):
        partner = self._partner("Actionable")
        self._draft(partner).write({"state": "pending"})
        mail = self._sent_mail(self.Outreach._cron_pending_digest)
        self.assertIn("/odoo/action-", mail.body_html)
        self.assertIn("Open the outreach queue", mail.body_html)

    def test_no_digest_when_there_is_nothing_pending(self):
        """An empty daily email teaches people to ignore the daily email."""
        self.Outreach.search([("state", "=", "pending")]).unlink()
        mail = self._sent_mail(self.Outreach._cron_pending_digest)
        self.assertFalse(mail)

    # ---- the watchdog -------------------------------------------------
    def _alert(self, severity="warning"):
        return self.env["era.ai.watchdog.alert"].sudo().create({
            "check_key": "k_%s" % severity, "name": "Outgoing email is failing",
            "severity": severity, "detail": "Check the mail server settings.",
        })

    def test_a_critical_alert_leads_with_customer_impact(self):
        alert = self._alert("critical")
        mail = self._sent_mail(
            lambda: self.env["era.ai.watchdog.alert"].sudo()._notify_owner(alert))
        self.assertIn("affecting customers now", mail.body_html)

    def test_a_warning_says_customers_are_fine(self):
        """Not every alert deserves the same alarm."""
        alert = self._alert("warning")
        mail = self._sent_mail(
            lambda: self.env["era.ai.watchdog.alert"].sudo()._notify_owner(alert))
        self.assertIn("Nothing is broken", mail.body_html)

    def test_critical_alerts_come_first(self):
        warning = self._alert("warning")
        warning.name = "A smaller thing"
        critical = self._alert("critical")
        critical.name = "The big thing"
        mail = self._sent_mail(
            lambda: self.env["era.ai.watchdog.alert"].sudo()._notify_owner(
                warning | critical))
        self.assertLess(mail.body_html.index("The big thing"),
                        mail.body_html.index("A smaller thing"))

    def test_the_watchdog_email_links_somewhere_useful(self):
        alert = self._alert()
        mail = self._sent_mail(
            lambda: self.env["era.ai.watchdog.alert"].sudo()._notify_owner(alert))
        self.assertIn("/odoo/action-", mail.body_html)
        self.assertIn("Open the watchdog", mail.body_html)

    def test_it_promises_not_to_repeat_itself(self):
        alert = self._alert()
        mail = self._sent_mail(
            lambda: self.env["era.ai.watchdog.alert"].sudo()._notify_owner(alert))
        self.assertIn("only hear about a problem once", mail.body_html)

    def test_the_cron_hands_the_notifier_a_recordset(self):
        """A list of records looks like a recordset until something sorts it."""
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        Alert.search([]).unlink()
        self.Watchlist.search([]).unlink()
        captured = {}
        original = type(Alert)._notify_owner

        def spy(records, alerts):
            captured["type"] = type(alerts).__name__
            captured["sortable"] = hasattr(alerts, "sorted")
            return original(records, alerts)

        with patch.object(type(Alert), "_notify_owner", spy), \
             patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            Alert._cron_watchdog()
        self.assertTrue(captured.get("sortable"),
                        "the cron passed %s" % captured.get("type"))


@tagged("post_install", "-at_install")
class TestEraAiMessageEveryone(EraAiCommon):
    """Writing to a whole audience at once, through the queue not around it.

    A bulk send is exactly when the guardrails matter most, so this deliberately
    creates ordinary drafts and submits them rather than mailing anyone.
    """

    def setUp(self):
        super().setUp()
        self.a = self._partner("Alpha Co")
        self.b = self._partner("Beta Co")
        self.watchlist = self.Watchlist.create({
            "name": "Quiet accounts",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', 'in', %s)]" % [self.a.id, self.b.id],
            "partner_field": "id", "play": "winback",
            "intent": "Ask what got in the way; cite their last order.",
        })

    def _wizard(self, **values):
        base = {"watchlist_id": self.watchlist.id, "subject": "Hello {name}",
                "body_html": "<p>Hello {name}, we miss you.</p>", "lang": "en_US"}
        base.update(values)
        return self.env["era.ai.watchlist.compose"].sudo().create(base)

    def test_it_drafts_one_message_per_contact(self):
        wizard = self._wizard()
        self.assertEqual(wizard.matched_count, 2)
        self.assertEqual(wizard.reachable_count, 2)
        wizard.action_create_drafts()
        drafts = self.Outreach.search([("watchlist_id", "=", self.watchlist.id)])
        self.assertEqual(len(drafts), 2)
        self.assertEqual(set(drafts.mapped("partner_id")), {self.a, self.b})
        self.assertEqual(set(drafts.mapped("play")), {"winback"})
        self.assertEqual(set(drafts.mapped("agent_name")), {"manual"})

    def test_the_name_placeholder_is_filled_per_contact(self):
        self._wizard().action_create_drafts()
        draft = self.Outreach.search(
            [("partner_id", "=", self.a.id), ("agent_name", "=", "manual")], limit=1)
        self.assertIn("Alpha Co", draft.body_html)
        self.assertNotIn("{name}", draft.body_html)

    def test_it_goes_through_the_queue_rather_than_around_it(self):
        """Ramp mode must hold a bulk send exactly like any other."""
        self._wizard().action_create_drafts()
        drafts = self.Outreach.search([("watchlist_id", "=", self.watchlist.id)])
        self.assertEqual(set(drafts.mapped("state")), {"pending"})
        self.assertFalse(any(drafts.mapped("sent_at")))

    def test_the_guardrails_still_apply_to_a_bulk_send(self):
        self.env["mail.blacklist"].sudo()._add(self.b.email)
        self._wizard().action_create_drafts()
        blocked = self.Outreach.search([
            ("watchlist_id", "=", self.watchlist.id),
            ("partner_id", "=", self.b.id)])
        self.assertEqual(blocked.state, "blocked")
        self.assertIn("blacklist", blocked.block_reason.lower())

    def test_contacts_without_an_email_are_counted_not_silently_dropped(self):
        self.b.email = False
        wizard = self._wizard()
        self.assertEqual(wizard.matched_count, 2)
        self.assertEqual(wizard.reachable_count, 1)
        self.assertIn("no email address", wizard.skipped_note)

    def test_someone_already_queued_is_not_written_to_twice(self):
        self._draft(self.a).write({"state": "pending"})
        wizard = self._wizard()
        self.assertEqual(wizard.reachable_count, 1)
        self.assertIn("already have a message waiting", wizard.skipped_note)

    def test_writing_to_nobody_refuses_rather_than_doing_nothing(self):
        self.a.email = False
        self.b.email = False
        with self.assertRaises(UserError):
            self._wizard().action_create_drafts()

    # ---- the AI writer ------------------------------------------------
    def _fake_ai(self, reply):
        """Stand in for ai.agent.get_direct_response, which is synchronous."""
        agent = type("FakeAgent", (), {
            "sudo": lambda self: self,
            "get_direct_response": lambda self, prompt: [reply],
        })()
        return patch.object(
            type(self.env["era.ai.watchlist.compose"]), "_copywriter",
            return_value=agent)

    def test_the_ai_fills_the_message_for_review_not_for_sending(self):
        wizard = self._wizard(subject=".", body_html="<p>.</p>")
        with self._fake_ai('{"subject": "We miss you", '
                           '"body_html": "<p>Hi {name}, everything ok?</p>"}'):
            result = wizard.action_write_with_ai()
        self.assertEqual(wizard.subject, "We miss you")
        self.assertIn("{name}", wizard.body_html)
        self.assertEqual(result["res_model"], "era.ai.watchlist.compose",
                         "it must reopen for review, not draft immediately")
        self.assertFalse(self.Outreach.search(
            [("watchlist_id", "=", self.watchlist.id)]))

    def test_a_fenced_reply_is_still_understood(self):
        wizard = self._wizard()
        with self._fake_ai('```json\n{"subject": "S", "body_html": "<p>B</p>"}\n```'):
            wizard.action_write_with_ai()
        self.assertEqual(wizard.subject, "S")

    def test_an_unusable_reply_is_reported_not_written(self):
        wizard = self._wizard(subject="Mine", body_html="<p>Mine</p>")
        with self._fake_ai("I'm afraid I can't do that"):
            with self.assertRaises(UserError):
                wizard.action_write_with_ai()
        self.assertEqual(wizard.subject, "Mine", "the user's text was destroyed")

    def test_a_provider_failure_is_explained_not_a_traceback(self):
        wizard = self._wizard()
        agent = type("Boom", (), {
            "sudo": lambda self: self,
            "get_direct_response": lambda self, prompt: (_ for _ in ()).throw(
                ValueError("no API key")),
        })()
        with patch.object(type(wizard), "_copywriter", return_value=agent):
            with self.assertRaises(UserError) as caught:
                wizard.action_write_with_ai()
        self.assertIn("no API key", str(caught.exception))

    def test_the_button_hides_where_odoo_ai_is_absent(self):
        """The module installs on Community, where ai.agent does not exist."""
        wizard = self._wizard()
        self.assertEqual(wizard.ai_available,
                         self.env.get("ai.agent") is not None)


@tagged("post_install", "-at_install")
class TestEraAiComposeProposesAndSends(EraAiCommon):
    """The wizard should arrive with the message already written.

    The watchlist's intent already says what the message must achieve, so
    asking the owner to describe it again is asking a question the system can
    answer. And in Full autonomy, queuing for approval ignores the instruction
    the owner already gave.
    """

    def setUp(self):
        super().setUp()
        self.a = self._partner("Gamma Co")
        self.watchlist = self.Watchlist.create({
            "name": "Lapsed", "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % self.a.id, "partner_field": "id",
            "play": "winback", "intent": "Ask what got in the way.",
        })
        self.Compose = self.env["era.ai.watchlist.compose"].sudo()

    def _fake_ai(self, reply='{"subject": "We miss you", '
                             '"body_html": "<p>Hi {name}</p>"}'):
        agent = type("FakeAgent", (), {
            "sudo": lambda self: self,
            "get_direct_response": lambda self, prompt: [reply],
        })()
        return patch.object(type(self.Compose), "_copywriter",
                            return_value=agent)

    def test_the_wizard_opens_with_the_message_already_written(self):
        with self._fake_ai():
            values = self.Compose.with_context(
                default_watchlist_id=self.watchlist.id
            ).default_get(["watchlist_id", "subject", "body_html", "ai_note"])
        self.assertEqual(values["subject"], "We miss you")
        self.assertIn("{name}", values["body_html"])
        self.assertIn("I wrote this", values["ai_note"])

    def test_opening_still_works_when_the_ai_is_down(self):
        """A dead provider must not leave a form that cannot be filled."""
        agent = type("Boom", (), {
            "sudo": lambda self: self,
            "get_direct_response": lambda self, p: (_ for _ in ()).throw(
                ValueError("no key")),
        })()
        with patch.object(type(self.Compose), "_copywriter", return_value=agent):
            values = self.Compose.with_context(
                default_watchlist_id=self.watchlist.id
            ).default_get(["watchlist_id", "subject", "body_html", "ai_note"])
        self.assertIn("could not write", values["ai_note"])
        self.assertFalse(values.get("body_html"))

    def test_the_message_is_no_longer_a_required_field(self):
        """Required + empty is what made it look like a question."""
        for name in ("subject", "body_html"):
            self.assertFalse(self.Compose._fields[name].required, name)

    def test_sending_an_empty_message_is_refused(self):
        wizard = self.Compose.create({
            "watchlist_id": self.watchlist.id, "lang": "en_US"})
        with self.assertRaises(UserError):
            wizard.action_create_drafts()

    def test_rewrite_replaces_the_text_and_stays_open(self):
        wizard = self.Compose.create({
            "watchlist_id": self.watchlist.id, "lang": "en_US",
            "subject": "Mine", "body_html": "<p>Mine</p>"})
        with self._fake_ai():
            result = wizard.action_write_with_ai()
        self.assertEqual(wizard.subject, "We miss you")
        self.assertEqual(result["res_model"], "era.ai.watchlist.compose")

    # ---- what the button does depends on the mode ---------------------
    def test_ramp_mode_queues_and_says_so(self):
        self.param.set_param("era_ai_manager.autonomy_mode", "ramp")
        wizard = self.Compose.create({
            "watchlist_id": self.watchlist.id, "lang": "en_US",
            "subject": "S", "body_html": "<p>B</p>"})
        self.assertFalse(wizard.will_send_now)
        self.assertIn("Ramp mode", wizard.autonomy_note)
        wizard.action_create_drafts()
        draft = self.Outreach.search([("partner_id", "=", self.a.id)], limit=1)
        self.assertEqual(draft.state, "pending")
        self.assertFalse(draft.sent_at)

    def test_full_autonomy_sends_immediately(self):
        """The owner already said they do not want to approve each one."""
        self.param.set_param("era_ai_manager.autonomy_mode", "full")
        wizard = self.Compose.create({
            "watchlist_id": self.watchlist.id, "lang": "en_US",
            "subject": "S", "body_html": "<p>B</p>"})
        self.assertTrue(wizard.will_send_now)
        self.assertIn("Full autonomy", wizard.autonomy_note)
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            result = wizard.action_create_drafts()
        draft = self.Outreach.search([("partner_id", "=", self.a.id)], limit=1)
        self.assertEqual(draft.state, "sent")
        self.assertTrue(draft.sent_at)
        self.assertIn("Sent to", result["name"])

    def test_full_autonomy_still_obeys_the_guardrails(self):
        """Sending without approval is not sending without limits."""
        self.param.set_param("era_ai_manager.autonomy_mode", "full")
        self.env["mail.blacklist"].sudo()._add(self.a.email)
        wizard = self.Compose.create({
            "watchlist_id": self.watchlist.id, "lang": "en_US",
            "subject": "S", "body_html": "<p>B</p>"})
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            result = wizard.action_create_drafts()
        draft = self.Outreach.search([("partner_id", "=", self.a.id)], limit=1)
        self.assertEqual(draft.state, "blocked")
        self.assertIn("blocked", result["name"])

    def test_the_copywriter_adopts_the_model_this_database_uses(self):
        """The field default needs an OpenAI key most deployments lack.

        On the Fatoratec database every working agent runs through a custom
        provider; a new agent left on the default failed with "no API key set
        for provider 'openai'" the first time it was called.
        """
        Agent = self.env.get("ai.agent")
        if Agent is None:
            self.skipTest("Odoo's AI app is not installed here")
        self.env["ir.config_parameter"].sudo().set_param(
            "era_ai_manager.copywriter_agent_id", "")
        existing = Agent.sudo().search([("active", "=", True)], order="id", limit=1)
        wizard = self.env["era.ai.watchlist.compose"].sudo().new(
            {"watchlist_id": self.watchlist.id})
        agent = wizard._copywriter()
        self.assertTrue(agent)
        if existing:
            self.assertEqual(agent.llm_model, existing.llm_model)
            # Where a provider add-on routes agents through its own accounts,
            # the link matters more than the model name: without it the agent
            # falls back to core and fails for a missing key.
            for field in ("era_account_id", "era_model_id"):
                if field in Agent._fields and existing[field]:
                    self.assertEqual(agent[field], existing[field], field)


@tagged("post_install", "-at_install")
class TestEraAiToManyAudience(EraAiCommon):
    """Some models keep their customer in a to-many field.

    Live chat's livechat_customer_partner_ids, event registrations, followers.
    Refusing those would mean the audience can be watched and never contacted,
    which is the least useful combination available.
    """

    def _watchlist(self, **values):
        base = {
            "name": "Via a to-many link",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('is_company', '=', True)]",
            "partner_field": "id", "play": "winback", "intent": "x",
        }
        base.update(values)
        return self.Watchlist.create(base)

    def test_a_to_many_partner_field_is_accepted(self):
        watchlist = self._watchlist(
            model_id=self.env.ref("base.model_res_partner").id,
            partner_field="child_ids", domain="[('is_company', '=', True)]")
        self.assertTrue(watchlist)

    def test_it_picks_the_first_contact_that_has_an_email(self):
        parent = self._partner("Parent Co")
        self.env["res.partner"].sudo().create({
            "name": "No email child", "parent_id": parent.id, "email": False})
        reachable = self.env["res.partner"].sudo().create({
            "name": "Reachable child", "parent_id": parent.id,
            "email": "child@example.test"})
        watchlist = self._watchlist(
            partner_field="child_ids", domain="[('id', '=', %s)]" % parent.id)
        self.assertEqual(watchlist.partner_of(parent), reachable)

    def test_it_falls_back_to_the_first_when_none_has_an_email(self):
        parent = self._partner("Silent Co")
        child = self.env["res.partner"].sudo().create({
            "name": "Silent child", "parent_id": parent.id, "email": False})
        watchlist = self._watchlist(
            partner_field="child_ids", domain="[('id', '=', %s)]" % parent.id)
        self.assertEqual(watchlist.partner_of(parent), child)

    def test_an_empty_to_many_yields_nobody_rather_than_an_error(self):
        lonely = self._partner("Childless Co")
        watchlist = self._watchlist(
            partner_field="child_ids", domain="[('id', '=', %s)]" % lonely.id)
        self.assertFalse(watchlist.partner_of(lonely))

    def test_a_field_that_is_not_a_contact_is_refused_on_save(self):
        """Pointing at the wrong field is how you end up writing to nobody —
        or worse, to your own operator."""
        with self.assertRaises(ValidationError):
            self._watchlist(partner_field="country_id")

    def test_a_live_chat_style_audience_can_be_built(self):
        """The case that prompted this: watch conversations, reach the human."""
        if self.env.get("discuss.channel") is None:
            self.skipTest("Discuss is not installed here")
        model = self.env["ir.model"].sudo().search(
            [("model", "=", "discuss.channel")], limit=1)
        field = self.env["discuss.channel"]._fields.get(
            "livechat_customer_partner_ids")
        if not field:
            self.skipTest("Live chat is not installed here")
        watchlist = self.Watchlist.create({
            "name": "Chats to follow up", "model_id": model.id,
            "domain": "[('channel_type', '=', 'livechat')]",
            "partner_field": "livechat_customer_partner_ids",
            "play": "chat_followup", "intent": "Follow up after the chat.",
        })
        self.assertTrue(watchlist)
        self.assertIsInstance(watchlist.match_count, int)


@tagged("post_install", "-at_install")
class TestEraAiUnreachableAudience(EraAiCommon):
    """An audience nobody can be written to should say so out loud.

    "5 match, 0 will get a draft" with no reason reads like a broken button.
    It is usually the honest shape of the data — anonymous live chat visitors
    being the case that raised it.
    """

    def _audience_without_contacts(self):
        orphan = self.env["res.partner"].sudo().create({
            "name": "Anonymous-ish", "email": False})
        watchlist = self.Watchlist.create({
            "name": "Nobody behind these",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % orphan.id,
            "partner_field": "child_ids",  # empty -> no contact at all
            "play": "winback", "intent": "x",
        })
        return self.env["era.ai.watchlist.compose"].sudo().create({
            "watchlist_id": watchlist.id, "subject": "s",
            "body_html": "<p>hi</p>", "lang": "en_US"})

    def test_it_counts_records_that_have_no_contact(self):
        wizard = self._audience_without_contacts()
        self.assertEqual(wizard.matched_count, 1)
        self.assertEqual(wizard.reachable_count, 0)
        self.assertTrue(wizard.skipped_note,
                        "A zero with no explanation looks like a bug")
        self.assertIn("anonymous", wizard.skipped_note.lower())

    def test_sending_refuses_and_explains_why(self):
        wizard = self._audience_without_contacts()
        with self.assertRaises(UserError) as caught:
            wizard.action_create_drafts()
        self.assertIn("anonymous", str(caught.exception).lower())

    def test_a_reachable_audience_is_unaffected(self):
        target = self._partner("Reachable One", email="reach@example.test")
        watchlist = self.Watchlist.create({
            "name": "Reachable", "state": "approved",
            "model_id": self.env.ref("base.model_res_partner").id,
            "domain": "[('id', '=', %s)]" % target.id,
            "partner_field": "id", "play": "winback", "intent": "x",
        })
        wizard = self.env["era.ai.watchlist.compose"].sudo().create({
            "watchlist_id": watchlist.id, "subject": "s",
            "body_html": "<p>hi {name}</p>", "lang": "en_US"})
        self.assertEqual(wizard.reachable_count, 1)
        self.assertFalse(wizard.skipped_note)


@tagged("post_install", "-at_install")
class TestEraAiConversationHarvest(EraAiCommon):
    """Reading a finished chat and turning it into something actionable."""

    def setUp(self):
        super().setUp()
        self.Conversation = self.env["era.ai.conversation"].sudo()
        # A real operator is a user, human or bot. Modelling it as a bare
        # partner made our own side look like the visitor's.
        self.operator_user = self.env["res.users"].sudo().create({
            "name": "Bot Operator", "login": "era.chat.operator",
            "email": "bot@ourcompany.test",
        })
        self.operator = self.operator_user.partner_id

    def _channel(self, exchange, name="Chat"):
        """exchange = [(is_visitor, text), ...]"""
        values = {"name": name}
        Channel = self.env["discuss.channel"]
        if "livechat_operator_id" in Channel._fields:
            values["livechat_operator_id"] = self.operator.id
        if self._livechat_available():
            values["channel_type"] = "livechat"
        channel = Channel.sudo().create(values)
        guest = None
        if self.env.get("mail.guest") is not None:
            guest = self.env["mail.guest"].sudo().create({"name": "Visitor #1"})
        for is_visitor, text in exchange:
            values = {
                "model": "discuss.channel", "res_id": channel.id,
                "body": "<p>%s</p>" % text, "message_type": "comment",
                "subtype_id": self.env.ref("mail.mt_comment").id,
            }
            if is_visitor and guest:
                values["author_guest_id"] = guest.id
            elif is_visitor:
                values["author_id"] = self._partner(
                    "Outside Visitor", email=False).id
            else:
                values["author_id"] = self.operator.id
            self.env["mail.message"].sudo().create(values)
        return channel

    def _read(self, exchange):
        channel = self._channel(exchange)
        return self.Conversation._read_conversation(channel)

    # -- extraction ---------------------------------------------------
    def test_it_takes_the_email_the_customer_typed(self):
        reading = self._read([
            (False, "What is your email?"),
            (True, "mine is real.person@customer.test thanks"),
        ])
        self.assertEqual(reading["email"], "real.person@customer.test")

    def test_it_ignores_the_example_the_assistant_offered(self):
        """The bug this rule exists for: harvesting the bot's own example
        would create a contact for a person who does not exist."""
        reading = self._read([
            (False, "Send your address, like name@example.com"),
            (True, "ok"),
        ])
        self.assertFalse(reading["email"])

    def test_it_ignores_an_address_only_our_side_said(self):
        reading = self._read([
            (False, "Write to us at support@ourcompany.test"),
            (True, "thanks"),
        ])
        self.assertFalse(reading["email"])

    def test_it_takes_a_phone_number_of_plausible_length(self):
        reading = self._read([(True, "call me on 0581796666")])
        self.assertEqual(reading["phone"], "0581796666")

    def test_it_does_not_mistake_a_short_number_for_a_phone(self):
        reading = self._read([(True, "I need 25 invoices")])
        self.assertFalse(reading["phone"])

    # -- signals ------------------------------------------------------
    def test_it_notices_the_assistant_answering_with_an_error(self):
        reading = self._read([
            (True, "How do I issue an invoice?"),
            (False, "Codex CLI error: Your access token could not be refreshed."),
        ])
        self.assertTrue(reading["assistant_failed"])

    def test_a_normal_answer_is_not_a_failure(self):
        reading = self._read([
            (True, "How do I issue an invoice?"),
            (False, "Open the app and press the plus button."),
        ])
        self.assertFalse(reading["assistant_failed"])

    def test_it_notices_when_the_customer_had_the_last_word(self):
        reading = self._read([
            (False, "Anything else?"),
            (True, "yes, I am still waiting"),
        ])
        self.assertTrue(reading["left_unanswered"])
        self.assertFalse(self._read([
            (True, "hello"), (False, "hello, how can I help?")])["left_unanswered"])

    def test_the_transcript_keeps_both_sides(self):
        reading = self._read([(True, "hello there"), (False, "hi back")])
        self.assertIn("hello there", reading["transcript"])
        self.assertIn("hi back", reading["transcript"])
        self.assertEqual(reading["message_count"], 2)

    # -- harvesting ---------------------------------------------------
    def test_harvesting_produces_a_record_with_the_details(self):
        channel = self._channel([
            (True, "What does it cost?"),
            (False, "What is your email?"),
            (True, "buyer@customer.test"),
        ])
        record = self.Conversation._harvest_one(channel)
        self.assertTrue(record)
        self.assertEqual(record.email, "buyer@customer.test")
        self.assertTrue(record.has_contact)
        self.assertTrue(record.visitor_name, "Every record needs a name to show")

    def test_the_same_chat_is_never_harvested_twice(self):
        """Odoo 19 dropped _sql_constraints; declared the old way this
        constraint silently did not exist and every cron run made a new lead."""
        channel = self._channel([(True, "hello, mine is dup@customer.test"),
                                 (False, "hi")])
        self.Conversation._harvest_one(channel)
        self.env.flush_all()
        with self.assertRaises(Exception):
            self.Conversation._harvest_one(channel)
            self.env.flush_all()

    def test_it_never_harvests_our_own_address(self):
        """Whoever typed it, our own support address is not a lead."""
        reading = self._read([(True, "I wrote to bot@ourcompany.test already")])
        self.assertFalse(reading["email"])

    def test_an_empty_chat_produces_nothing(self):
        channel = self.env["discuss.channel"].sudo().create({"name": "Silent"})
        self.assertFalse(self.Conversation._harvest_one(channel))

    # -- conversion ---------------------------------------------------
    def _harvested(self, **values):
        base = {
            "title": "Chat", "thread_model": "discuss.channel",
            "thread_id": 999999, "transcript": "Visitor: what does it cost?",
            "visitor_name": "A Buyer", "interest": "Asked about pricing",
            "summary": "Wanted the price.", "kind": "sales",
        }
        base.update(values)
        return self.Conversation.create(base)

    def test_a_visitor_with_an_email_becomes_a_contact_and_a_follow_up(self):
        record = self._harvested(email="newbuyer@customer.test", thread_id=1001)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_convert()
        self.assertEqual(record.state, "converted")
        self.assertTrue(record.partner_id)
        self.assertEqual(record.partner_id.email, "newbuyer@customer.test")
        self.assertTrue(record._result_record())

    def test_it_reuses_an_existing_contact_rather_than_duplicating(self):
        existing = self._partner("Known Co", email="known@customer.test")
        record = self._harvested(email="known@customer.test", thread_id=1002)
        self.assertEqual(record._ensure_partner(), existing)

    def test_a_visitor_who_left_nothing_cannot_become_a_lead(self):
        """The honest refusal: no email, no phone, no follow-up."""
        record = self._harvested(thread_id=1003, email=False, phone=False)
        with self.assertRaises(UserError):
            record.action_convert()
        self.assertFalse(record.partner_id)

    def test_noise_is_refused(self):
        record = self._harvested(thread_id=1004, kind="noise",
                                 email="x@customer.test")
        with self.assertRaises(UserError):
            record.action_convert()

    def test_converting_twice_does_not_create_two_leads(self):
        record = self._harvested(email="twice@customer.test", thread_id=1005)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_convert()
        first = record.result_id
        record.action_convert()
        self.assertEqual(record.result_id, first)

    def test_a_support_issue_prefers_helpdesk_when_it_exists(self):
        record = self._harvested(thread_id=1006, kind="support",
                                 email="broken@customer.test")
        chosen = record._follow_up_model()
        if self.env.get("helpdesk.ticket") is not None:
            self.assertEqual(chosen, "helpdesk.ticket")
        elif self.env.get("crm.lead") is not None:
            self.assertEqual(chosen, "crm.lead")

    def test_the_transcript_travels_with_the_follow_up(self):
        record = self._harvested(email="reader@customer.test", thread_id=1007)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_convert()
        target = record._result_record()
        body_field = record._body_field(target)
        if body_field:
            self.assertIn("what does it cost", str(target[body_field]).lower())

    # -- the watchdog -------------------------------------------------
    def test_a_broken_assistant_reaches_the_owner(self):
        """Counted from the channels, not from the filed records: the chats
        that expose a broken assistant are mostly the anonymous ones, and
        those are deliberately never filed."""
        if not self._livechat_available():
            self.skipTest("Live chat is not installed here")
        self._channel([
            (True, "hello"),
            (False, "Incorrect API key provided: sk-xxx"),
        ])
        keys = {key for key, _n, _s, _d in
                self.env["era.ai.watchdog.alert"]._run_checks()}
        self.assertIn("assistant_failed", keys)

    def test_no_broken_chats_no_alert(self):
        keys = {key for key, _n, _s, _d in
                self.env["era.ai.watchdog.alert"]._run_checks()}
        self.assertNotIn("assistant_failed", keys)

    def test_the_visitor_is_not_labelled_with_the_assistant_name(self):
        """Live chat names the channel after the operator. Falling back to it
        labelled every anonymous visitor as the bot that failed them."""
        channel = self._channel([(True, "hello, I am at me@customer.test"),
                                 (False, "hi")], name="Bot Operator")
        record = self.Conversation._harvest_one(channel)
        self.assertNotEqual(record.visitor_name, "Bot Operator")
        self.assertTrue(record.visitor_name)

    def test_the_visitor_label_comes_from_the_visitor(self):
        reading = self._read([(True, "hello")])
        self.assertTrue(reading["visitor_label"])
        self.assertNotIn("Bot Operator", reading["visitor_label"])


@tagged("post_install", "-at_install")
class TestEraAiStandingFaultsAreReported(EraAiCommon):
    """A problem announced once and never again looks like a problem fixed.

    The watchdog deliberately emails each fault once. That rule is right for
    noise and wrong for silence: an assistant failing all week reads exactly
    like an assistant repaired on Monday.
    """

    def setUp(self):
        super().setUp()
        self.param.set_param("era_ai_manager.owner_email", "owner@example.test")
        self.Alert = self.env["era.ai.watchdog.alert"].sudo()

    def _fault(self, severity="critical", name="The assistant is failing"):
        return self.Alert.create({
            "check_key": "assistant_failed", "name": name,
            "severity": severity, "detail": "Check the provider key.",
        })

    def _report(self):
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            self.Outreach._cron_pending_digest()
        return self.env["mail.mail"].sudo().search(
            [("email_to", "=", "owner@example.test")], order="id desc", limit=1)

    def test_an_open_fault_appears_in_the_daily_report(self):
        self._fault()
        mail = self._report()
        self.assertTrue(mail, "the report was not sent")
        self.assertIn("The assistant is failing", mail.body_html)
        self.assertIn("Still not fixed", mail.body_html)

    def test_it_reports_faults_even_in_full_autonomy(self):
        """Full autonomy is the mode the owner ends up in. Reporting nothing
        there means the routine email stops exactly when they stop watching."""
        self.param.set_param("era_ai_manager.autonomy_mode", "full")
        self._fault()
        mail = self._report()
        self.assertTrue(mail)
        self.assertIn("The assistant is failing", mail.body_html)

    def test_a_resolved_fault_stops_being_mentioned(self):
        fault = self._fault()
        fault.write({"state": "resolved"})
        self.Outreach.search([("state", "=", "pending")]).unlink()
        self.assertFalse(self._report())

    def test_nothing_pending_and_nothing_broken_sends_nothing(self):
        self.Outreach.search([("state", "=", "pending")]).unlink()
        self.Alert.search([]).unlink()
        self.assertFalse(self._report())

    def test_the_report_says_how_long_it_has_been_broken(self):
        fault = self._fault()
        self.env.cr.execute(
            "UPDATE era_ai_watchdog_alert SET create_date = create_date - "
            "interval '3 days' WHERE id = %s", (fault.id,))
        fault.invalidate_recordset()
        self.assertIn("3 day", self.Alert.open_summary_html())

    def test_critical_faults_are_listed_before_warnings(self):
        self.Alert.search([]).unlink()
        self._fault(severity="warning", name="A small thing")
        self._fault(severity="critical", name="A broken thing")
        summary = self.Alert.open_summary_html()
        self.assertLess(summary.index("A broken thing"),
                        summary.index("A small thing"))

    def test_the_subject_says_what_is_inside(self):
        self.Outreach.search([("state", "=", "pending")]).unlink()
        self._fault()
        self.assertIn("still need fixing", self._report().subject)

    def test_both_pending_and_broken_are_carried_together(self):
        self.param.set_param("era_ai_manager.autonomy_mode", "ramp")
        partner = self._partner("Waiting Co")
        self._draft(partner, subject="A queued note").write({"state": "pending"})
        self._fault()
        mail = self._report()
        self.assertIn("A queued note", mail.body_html)
        self.assertIn("The assistant is failing", mail.body_html)

    def test_the_digest_counts_open_faults(self):
        self.Alert.search([]).unlink()
        self._fault()
        digest = self.env["digest.digest"].sudo().search([], limit=1)
        if not digest:
            self.skipTest("No digest configured here")
        digest.invalidate_recordset()
        self.assertEqual(digest.kpi_era_ai_faults_value, 1)

    def test_an_open_alert_keeps_its_numbers_current(self):
        """A stored count is a snapshot. Left alone it reports yesterday's
        number for ever, which is worse than reporting none."""
        alert = self.Alert.create({
            "check_key": "mail_exception", "name": "1 outgoing email(s) failed",
            "severity": "critical", "detail": "old detail",
        })
        for index in range(3):
            self.env["mail.mail"].sudo().create({
                "subject": "stuck %s" % index, "state": "exception",
                "email_to": "x@example.test",
            })
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            self.Alert._cron_watchdog()
        alert.invalidate_recordset()
        self.assertNotEqual(alert.name, "1 outgoing email(s) failed",
                            "the alert still reports the old count")
        self.assertEqual(alert.state, "open", "it should be updated, not reopened")
        self.assertEqual(self.Alert.search_count(
            [("check_key", "=", "mail_exception"), ("state", "=", "open")]), 1,
            "refreshing must not create a second alert")

    def test_refreshing_does_not_re_nag_the_owner(self):
        """The one-off rule still holds: an updated alert is not a new one."""
        self.Alert.create({
            "check_key": "mail_exception", "name": "1 outgoing email(s) failed",
            "severity": "critical", "detail": "old",
        })
        self.env["mail.mail"].sudo().create({
            "subject": "stuck", "state": "exception", "email_to": "x@example.test"})
        with patch.object(type(self.env["mail.mail"].sudo()), "send",
                          return_value=True):
            self.Alert._cron_watchdog()
        # Other checks may legitimately raise their own first-time alerts;
        # what must not happen is this one being announced again.
        for mail in self.env["mail.mail"].sudo().search(
                [("email_to", "=", "owner@example.test")]):
            self.assertNotIn("outgoing email", mail.body_html or "",
                             "an already-reported fault was announced again")


@tagged("post_install", "-at_install")
class TestEraAiOnlyReachableChatsAreFiled(EraAiCommon):
    """A row nobody can act on is not a task, it is a chore.

    An anonymous visitor with no address and no number cannot be answered,
    filed or followed up. Listing them for review asks someone to open a
    record and close it again.
    """

    def setUp(self):
        super().setUp()
        self.Conversation = self.env["era.ai.conversation"].sudo()
        self.operator_user = self.env["res.users"].sudo().create({
            "name": "Chat Bot", "login": "era.reach.operator",
            "email": "bot@ourcompany.test",
        })
        self.operator = self.operator_user.partner_id

    def _channel(self, exchange, customer=None):
        values = {"name": "Chat"}
        Channel = self.env["discuss.channel"]
        if "livechat_operator_id" in Channel._fields:
            values["livechat_operator_id"] = self.operator.id
        if self._livechat_available():
            values["channel_type"] = "livechat"
        channel = Channel.sudo().create(values)
        guest = None
        if self.env.get("mail.guest") is not None and not customer:
            guest = self.env["mail.guest"].sudo().create({"name": "Visitor #9"})
        for is_visitor, text in exchange:
            values = {
                "model": "discuss.channel", "res_id": channel.id,
                "body": "<p>%s</p>" % text, "message_type": "comment",
                "subtype_id": self.env.ref("mail.mt_comment").id,
            }
            if is_visitor and customer:
                values["author_id"] = customer.id
            elif is_visitor and guest:
                values["author_guest_id"] = guest.id
            elif is_visitor:
                values["author_id"] = self._partner("Outsider", email=False).id
            else:
                values["author_id"] = self.operator.id
            self.env["mail.message"].sudo().create(values)
        return channel

    def test_an_anonymous_chat_is_not_filed_at_all(self):
        channel = self._channel([
            (True, "how do I make an invoice?"),
            (False, "Codex CLI error: token could not be refreshed"),
        ])
        self.assertFalse(self.Conversation._harvest_one(channel),
                         "an unanswerable chat should not reach the review list")

    def test_a_chat_with_an_email_is_filed(self):
        channel = self._channel([
            (True, "my address is buyer@customer.test"), (False, "thanks")])
        self.assertTrue(self.Conversation._harvest_one(channel))

    def test_a_chat_with_only_a_phone_is_filed(self):
        channel = self._channel([(True, "call me on 0581796666"), (False, "ok")])
        record = self.Conversation._harvest_one(channel)
        self.assertTrue(record)
        self.assertTrue(record.has_contact)

    def test_a_chat_from_a_known_contact_is_filed_without_an_email_in_the_text(self):
        known = self._partner("Known Buyer", email="known.buyer@customer.test")
        channel = self._channel([(True, "I have a problem"), (False, "hello")],
                                customer=known)
        record = self.Conversation._harvest_one(channel)
        if "livechat_customer_partner_ids" not in channel._fields:
            self.skipTest("Live chat is not installed here")
        self.assertTrue(record)
        self.assertEqual(record._reply_address(), "known.buyer@customer.test")

    def test_the_health_check_still_counts_anonymous_breakages(self):
        """The reason this is read from the channels, not the records: an
        error message is what stops a visitor before they leave an address,
        so the worst failures are exactly the ones no longer filed."""
        if not self._livechat_available():
            self.skipTest("Live chat is not installed here")
        self._channel([
            (True, "hello"),
            (False, "Incorrect API key provided: sk-xxx"),
        ])
        self.assertGreaterEqual(
            self.Conversation.count_broken_conversations(days=7), 1)
        keys = {key for key, _n, _s, _d in
                self.env["era.ai.watchdog.alert"]._run_checks()}
        self.assertIn("assistant_failed", keys)

    def test_it_catches_the_wording_that_slipped_through_in_production(self):
        """Found live: the assistant said "The Codex CLI returned an empty
        response" and the health check scored the day as healthy."""
        reading = self.Conversation._read_conversation(self._channel([
            (True, "I need help"),
            (False, "The Codex CLI returned an empty response."),
        ]))
        self.assertTrue(reading["assistant_failed"])

    def test_a_deployment_can_teach_it_a_new_wording(self):
        """Any list of literals is incomplete; waiting for a release to add
        one means a week of unanswered customers."""
        self.env["ir.config_parameter"].sudo().set_param(
            "era_ai_manager.breakage_markers", "widget malfunctioned")
        reading = self.Conversation._read_conversation(self._channel([
            (True, "hello"), (False, "The widget malfunctioned, sorry.")]))
        self.assertTrue(reading["assistant_failed"])

    def test_a_healthy_chat_is_not_counted_as_broken(self):
        self._channel([(True, "hello"), (False, "hello, how can I help?")])
        self.assertEqual(
            self.Conversation.count_broken_conversations(days=7), 0)


@tagged("post_install", "-at_install")
class TestEraAiChatsGetAnswered(EraAiCommon):
    """Filing a conversation is not answering it."""

    def setUp(self):
        super().setUp()
        self.Conversation = self.env["era.ai.conversation"].sudo()

    def _record(self, **values):
        base = {
            "title": "Chat", "thread_model": "discuss.channel",
            "thread_id": 500001, "transcript": "Visitor: how do I start?",
            "visitor_name": "A Customer", "interest": "How to get started",
            "summary": "Wanted to know how to start.", "kind": "support",
            "email": "asker@customer.test",
        }
        base.update(values)
        return self.Conversation.create(base)

    def test_converting_also_queues_an_answer(self):
        record = self._record(thread_id=500002)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_convert()
        reply = self.Outreach.sudo().search(
            [("play", "=", "reply"), ("email_to", "=", "asker@customer.test")])
        self.assertTrue(reply, "the customer was filed but never answered")
        self.assertTrue(reply.body_html)

    def test_the_answer_goes_into_the_ticket_thread_when_there_is_one(self):
        record = self._record(thread_id=500003)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_convert()
        reply = self.Outreach.sudo().search(
            [("play", "=", "reply"), ("thread_id", "=", record.result_id)], limit=1)
        self.assertTrue(reply)
        self.assertEqual(reply.channel, "thread_reply")
        self.assertEqual(reply.thread_model, record.result_model)

    def test_the_answer_is_a_reply_and_so_escapes_the_marketing_cap(self):
        """They wrote to us first. Answering is not solicitation."""
        record = self._record(thread_id=500004)
        record._queue_reply()
        reply = self.Outreach.sudo().search(
            [("thread_id", "=", record.id),
             ("thread_model", "=", "era.ai.conversation")], limit=1)
        self.assertIn(reply.play, REPLY_PLAYS)

    def test_a_customer_is_never_answered_twice(self):
        record = self._record(thread_id=500005)
        first = record._queue_reply()
        second = record._queue_reply()
        self.assertEqual(first, second)

    def test_a_phone_only_conversation_cannot_be_answered_by_email(self):
        record = self._record(thread_id=500006, email=False, phone="0581796666")
        self.assertFalse(record._queue_reply())
        with self.assertRaises(UserError):
            record.action_reply()

    def test_it_answers_on_the_contact_address_when_the_chat_had_none(self):
        known = self._partner("Silent Chatter", email="silent@customer.test")
        record = self._record(thread_id=500007, email=False,
                              partner_id=known.id)
        self.assertEqual(record._reply_address(), "silent@customer.test")
        self.assertTrue(record._queue_reply())

    def test_the_fallback_answer_admits_the_chat_ended_badly(self):
        """With no AI configured the message must still be honest and useful."""
        record = self._record(thread_id=500008)
        subject, body = record._draft_reply()
        self.assertTrue(subject)
        self.assertIn("reply", body.lower())

    def test_answering_files_the_conversation_first(self):
        record = self._record(thread_id=500009)
        if not record._follow_up_model():
            self.skipTest("Neither CRM nor Helpdesk installed here")
        record.action_reply()
        self.assertEqual(record.state, "converted")
        self.assertTrue(record._result_record())

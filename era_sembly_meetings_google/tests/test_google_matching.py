# -*- coding: utf-8 -*-
"""Google as a second provider.

The one genuinely new problem Google brings is that it carries NO Sembly id,
so a Drive recording has to be matched onto its meeting by time and title.
That heuristic is what these tests pin, in both directions: it must match the
obvious case, and it must REFUSE rather than guess when the evidence is thin —
a wrong match files one meeting's recording on another meeting's opportunity.
"""
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_sembly_meetings.tests import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyGoogle(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Meeting = self.env['sembly.meeting']
        self.now = fields.Datetime.now()

    def _meeting(self, sembly_id, title, minutes_ago=0, **kwargs):
        """The start time goes INTO the payload, not written afterwards.

        Adoption of an orphan Google record runs during the upsert, using the
        time the payload carried — which is how production works, since Sembly
        sends the real start. Setting it after the fact made every adoption test
        match against the fixture's date instead.
        """
        started = self.now - timedelta(minutes=minutes_ago)
        values = dict(fixtures.LIST_MEETINGS_META, id=sembly_id, title=title,
                      started_at=fields.Datetime.to_string(started))
        # A summary has to arrive WITH the payload, like Sembly sends it —
        # adoption reads it during the upsert, so writing it afterwards is too
        # late for the merge to be chosen.
        details = None
        if kwargs.pop('with_summary', False):
            details = {'minutes': [{'type': 'GENERIC',
                                    'text': 'Sembly: decisions, tasks, risks.'}]}
        meeting = self.Meeting._upsert_from_mcp(values, details)
        if kwargs:
            meeting.sudo().with_context(sembly_sync=True).write(kwargs)
        return meeting

    def _meet_name(self, title, minutes_ago=0, tz='+03:00'):
        """A file name in Meet's real format, with the meeting start in it."""
        from datetime import timedelta as _td
        offset = int(tz[1:3])
        local = self.now - _td(minutes=minutes_ago) + _td(hours=offset)
        return "%s - %s GMT%s - Recording" % (
            title, local.strftime('%Y/%m/%d %H:%M'), tz)

    def _recording(self, name, minutes_ago=0, file_id='drive-1'):
        return {
            'id': file_id,
            'name': name,
            'createdTime': fields.Datetime.to_string(
                self.now - timedelta(minutes=minutes_ago)).replace(' ', 'T') + 'Z',
            'webViewLink': 'https://drive.google.com/file/d/%s/view' % file_id,
            'videoMediaMetadata': {'durationMillis': '3480000'},
        }

    # ---------------------------------------------------------------- matching
    def test_a_recording_lands_on_the_meeting_it_belongs_to(self):
        meeting = self._meeting(910001, "Acme rollout review", minutes_ago=5)
        matched = self.Meeting._upsert_from_google(
            self._recording("Acme rollout review (2026-08-12)"), 'yasser@era.net.sa')
        self.assertEqual(matched, meeting)
        self.assertEqual(meeting.google_file_id, 'drive-1')
        self.assertTrue(meeting.has_google)
        self.assertEqual(meeting.provider_summary, "Sembly + Google")

    def test_a_recording_with_no_counterpart_becomes_its_own_meeting(self):
        """This is what makes Google usable as a provider ON ITS OWN."""
        created = self.Meeting._upsert_from_google(
            self._recording("Standalone session", minutes_ago=60 * 24 * 30,
                            file_id='drive-solo'), 'yasser@era.net.sa')
        self.assertTrue(created)
        self.assertEqual(created.source, 'google')
        self.assertFalse(created.sembly_meeting_id_is_real())
        self.assertEqual(created.provider_summary, "Google")
        self.assertEqual(created.duration_seconds, 3480)

    def test_it_refuses_to_guess_between_two_meetings(self):
        """A wrong match files one meeting's recording on another meeting's
        opportunity — worse than leaving it for a human."""
        self._meeting(910002, "Weekly sync", minutes_ago=3)
        self._meeting(910003, "Budget call", minutes_ago=4)
        matched = self.Meeting._match_google_artifact(
            "Recording 2026-08-12", self.now, None)
        self.assertFalse(matched)

    def test_the_title_breaks_a_tie(self):
        wanted = self._meeting(910004, "Falcon warehouse handover", minutes_ago=3)
        self._meeting(910005, "Budget call", minutes_ago=4)
        matched = self.Meeting._match_google_artifact(
            "Falcon warehouse handover", self.now, None)
        self.assertEqual(matched, wanted)

    def test_a_recording_far_from_any_meeting_matches_nothing(self):
        self._meeting(910006, "Morning stand-up", minutes_ago=600)
        self.assertFalse(self.Meeting._match_google_artifact(
            "Morning stand-up", self.now, None))

    def test_re_importing_the_same_recording_updates_in_place(self):
        meeting = self._meeting(910007, "Repeat import", minutes_ago=2)
        first = self.Meeting._upsert_from_google(
            self._recording("Repeat import", file_id='drive-2'), 'y@era.net.sa')
        second = self.Meeting._upsert_from_google(
            self._recording("Repeat import", file_id='drive-2'), 'y@era.net.sa')
        self.assertEqual(first, meeting)
        self.assertEqual(second, meeting)
        self.assertEqual(self.Meeting.search_count(
            [('google_file_id', '=', 'drive-2')]), 1)

    # ------------------------------------------------------------------ gemini
    def test_gemini_notes_are_stored_and_translated(self):
        meeting = self._meeting(910008, "English planning call", minutes_ago=2)
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>خطة الإطلاق أُقرّت.</p>") as ask:
            meeting._apply_gemini_notes("The launch plan was agreed.", 'doc-1')
        ask.assert_called_once()
        self.assertIn("launch plan", meeting.gemini_notes)
        self.assertIn("خطة الإطلاق", meeting.gemini_notes_ar)
        self.assertEqual(meeting.google_notes_file_id, 'doc-1')

    def test_a_failed_translation_keeps_the_original_notes(self):
        """The translation is an improvement, never a gate."""
        meeting = self._meeting(910009, "English review", minutes_ago=2)
        with patch.object(type(self.Meeting), '_ask_agent',
                          side_effect=ValueError("provider down")):
            meeting._apply_gemini_notes("Original English notes.", 'doc-2')
        self.assertIn("Original English notes", meeting.gemini_notes)
        self.assertFalse(meeting.gemini_notes_ar)

    def test_translation_can_be_switched_off(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.google_translate_notes', '0')
        meeting = self._meeting(910010, "English review", minutes_ago=2)
        with patch.object(type(self.Meeting), '_ask_agent') as ask:
            meeting._apply_gemini_notes("Notes.", 'doc-3')
        ask.assert_not_called()
        self.assertTrue(meeting.gemini_notes)

    # ----------------------------------------------------------------- guards
    def test_nothing_reaches_google_until_it_is_enabled(self):
        """Installing the module must not start calling out."""
        self.assertFalse(self.Meeting._google_enabled())
        with patch.object(type(self.Meeting), '_google_client') as client:
            self.Meeting._cron_sync_google()
        client.assert_not_called()

    def test_enabling_without_a_key_is_still_disabled(self):
        self.env['ir.config_parameter'].sudo().set_param('sembly.google_enabled', '1')
        self.assertFalse(self.Meeting._google_enabled(),
                         "a flag with no credential must not count as enabled")

    def test_google_fields_are_manager_only_content(self):
        """The Drive ids and Gemini notes are as much 'what the meeting says'
        as the summary is, so they follow the same rule."""
        content = self.Meeting._sembly_content_fields()
        for name in ('google_file_id', 'gemini_notes', 'gemini_notes_ar'):
            self.assertIn(name, content)

    # ------------------------------------------------------- one Arabic summary
    def test_both_summaries_are_merged_in_a_single_call(self):
        """Merging beats translating: the two providers watched the same meeting
        from different angles, so each covers what the other missed. And one
        merge costs exactly what one translation costs."""
        meeting = self._meeting(920001, "Zaghibi fuel stations", minutes_ago=2)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'summary': "<p>Sembly: decisions and risks.</p>"})
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<h4>الملخص</h4><p>مدموج.</p>") as ask:
            meeting._apply_gemini_notes("Gemini prose about the same meeting.")
        ask.assert_called_once()
        prompt = ask.call_args[0][0]
        self.assertIn("ملخص Sembly", prompt)
        self.assertIn("ملاحظات Gemini", prompt)
        self.assertIn("Gemini prose", prompt)
        self.assertEqual(meeting.merged_summary_source, 'merged')

    def test_with_no_sembly_summary_it_translates_instead(self):
        meeting = self._meeting(920002, "Google-only meeting", minutes_ago=2)
        meeting.sudo().with_context(sembly_sync=True).write({'summary': False})
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>مترجم.</p>") as ask:
            meeting._apply_gemini_notes("English notes only.")
        prompt = ask.call_args[0][0]
        self.assertIn("ترجم", prompt)
        self.assertNotIn("ملخص Sembly", prompt)
        self.assertEqual(meeting.merged_summary_source, 'translated')

    def test_neither_provider_summary_is_overwritten(self):
        """The Arabic text is a THIRD artefact: each provider's own words stay
        exactly as that provider sent them."""
        meeting = self._meeting(920003, "Keep both", minutes_ago=2)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'summary': "<p>Sembly original.</p>"})
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>مدموج.</p>"):
            meeting._apply_gemini_notes("Gemini text.")
        self.assertIn("Sembly original", meeting.summary)
        self.assertIn("Gemini text", meeting.gemini_notes)
        self.assertIn("مدموج", meeting.gemini_notes_ar)

    # ------------------------------------------------------------- narrow sweep
    def test_only_the_configured_account_is_impersonated(self):
        """Not every employee: files.list returns what the account can SEE,
        including files shared with it. Measured here, one account sees 1,108
        recordings while owning only 77."""
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('sembly.google_subject', 'crm@era.net.sa')
        icp.set_param('sembly.google_subjects', '')
        self.assertEqual(self.Meeting._google_subjects(), ['crm@era.net.sa'])

    def test_an_explicit_list_still_wins(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'sembly.google_subjects', 'a@era.net.sa, b@era.net.sa')
        self.assertEqual(self.Meeting._google_subjects(),
                         ['a@era.net.sa', 'b@era.net.sa'])

    # ---------------------------------------------------------------- backfill
    def test_the_drive_backfill_is_armed_by_a_human_and_resumes_on_a_token(self):
        cron = self.env.ref('era_sembly_meetings_google.cron_google_backfill')
        self.assertFalse(cron.active, "it must ship disarmed")
        self.Meeting._start_google_backfill()
        self.assertTrue(cron.active)
        icp = self.env['ir.config_parameter'].sudo()
        self.assertEqual(icp.get_param('sembly.google_backfill_state'), 'running')
        # Odoo returns False for an empty parameter, and the cron reads it as
        # `or None` — so an empty token means "start from the first page".
        self.assertFalse(icp.get_param('sembly.google_backfill_token'))

    def test_an_unarmed_backfill_calls_nobody(self):
        with patch.object(type(self.Meeting), '_google_client') as client:
            self.Meeting._cron_google_backfill()
            self.Meeting._cron_google_notes_backfill()
        client.assert_not_called()

    # ------------------------------------------- the filename is the real clock
    def test_the_meeting_start_is_read_out_of_the_file_name(self):
        """Drive's createdTime is when the UPLOAD finished, not when the meeting
        happened. Measured over 635 real recordings: median 75 minutes later,
        p90 181, max 457, never earlier. So 98% of them fall outside any sane
        window around createdTime, and the name is the only trustworthy key."""
        start = self.Meeting._meeting_start_from_name(
            "شركة الزغيبي لمحطات الوقود - 2026/08/12 13:52 GMT+03:00 - Recording")
        self.assertEqual(fields.Datetime.to_string(start), "2026-08-12 10:52:00")

    def test_the_timezone_in_the_name_is_honoured(self):
        """This workspace mixes GMT+03:00 and GMT+04:00; ignoring the offset
        would import a whole hour wrong."""
        self.assertEqual(
            fields.Datetime.to_string(self.Meeting._meeting_start_from_name(
                "X - 2026/08/11 15:21 GMT+04:00 - Recording")),
            "2026-08-11 11:21:00")

    def test_a_name_without_a_timestamp_returns_nothing(self):
        """36% of real names carry none, and inventing one would be worse than
        falling back to createdTime with a wide tolerance."""
        self.assertIsNone(self.Meeting._meeting_start_from_name("Recording (7)"))
        self.assertIsNone(self.Meeting._meeting_start_from_name(""))

    def test_a_late_upload_still_matches_its_meeting(self):
        """THE regression this whole change exists for: the sample recording was
        uploaded 68 minutes after its meeting and matched nothing."""
        meeting = self._meeting(940001, "Zaghibi fuel stations", minutes_ago=0)
        recording = {
            'id': 'drive-late',
            'name': self._meet_name("Zaghibi fuel stations", minutes_ago=0),
            # uploaded 68 minutes later, as Drive really reports it
            'createdTime': fields.Datetime.to_string(
                self.now + timedelta(minutes=68)).replace(' ', 'T') + 'Z',
            'webViewLink': 'https://drive.google.com/file/d/drive-late/view',
        }
        matched = self.Meeting._upsert_from_google(recording, 'crm@era.net.sa')
        self.assertEqual(matched, meeting,
                         "a 68-minute upload lag must not break the match")

    def test_a_google_only_record_carries_the_meeting_time_not_the_upload_time(self):
        recording = {
            'id': 'drive-solo-time',
            'name': "Solo meeting - 2026/08/12 13:52 GMT+03:00 - Recording",
            'createdTime': '2026-08-12T12:00:18Z',
            'webViewLink': 'https://x/view',
        }
        created = self.Meeting._upsert_from_google(recording, 'crm@era.net.sa')
        self.assertEqual(fields.Datetime.to_string(created.started_at),
                         "2026-08-12 10:52:00",
                         "the record must sit at the meeting's hour, not the upload's")

    # -------------------------------------------- Sembly arriving second
    def _orphan(self, title, minutes_ago=0, file_id='drive-orphan', **extra):
        """A Google-only record, as created when the recording arrives first."""
        recording = dict({
            'id': file_id,
            'name': self._meet_name(title, minutes_ago=minutes_ago),
            'createdTime': fields.Datetime.to_string(
                self.now + timedelta(minutes=68)).replace(' ', 'T') + 'Z',
            'webViewLink': 'https://drive.google.com/file/d/%s/view' % file_id,
        }, **extra)
        return self.Meeting._upsert_from_google(recording, 'crm@era.net.sa')

    def test_sembly_arriving_late_adopts_the_orphan_recording(self):
        """Sembly holds a meeting back while it processes it, so the recording
        is routinely in Drive first. Without this, one meeting ends up as two
        records and the recording is stranded on the one with no summary."""
        orphan = self._orphan("Zaghibi fuel stations", minutes_ago=0)
        self.assertFalse(orphan.sembly_meeting_id_is_real())
        orphan_id = orphan.id

        arrived = self._meeting(950001, "Zaghibi fuel stations", minutes_ago=0)

        self.assertEqual(arrived.google_file_id, 'drive-orphan',
                         "the recording must move onto the Sembly record")
        self.assertFalse(self.Meeting.browse(orphan_id).exists(),
                         "the orphan must be gone, not left as a duplicate")

    def test_the_sembly_record_survives_not_the_google_one(self):
        """The Sembly record is the one the matcher, the chatter and every link
        field already know about, and it may carry a human's hand-made link."""
        self._orphan("Balanced performance", minutes_ago=0, file_id='drive-keep')
        arrived = self._meeting(950002, "Balanced performance", minutes_ago=0)
        self.assertTrue(arrived.sembly_meeting_id_is_real())
        self.assertEqual(arrived.sembly_meeting_id, '950002')
        self.assertEqual(arrived.google_file_id, 'drive-keep')

    def test_two_candidate_orphans_are_left_for_a_person(self):
        """Merging the wrong recording into a meeting is worse than a duplicate,
        so ambiguity is reported rather than resolved."""
        self._orphan("First session", minutes_ago=1, file_id='drive-a')
        self._orphan("Second session", minutes_ago=2, file_id='drive-b')
        arrived = self._meeting(950003, "Some session", minutes_ago=0)
        self.assertFalse(arrived.google_file_id)
        log = self.env['sembly.sync.log'].search(
            [('channel', '=', 'google'), ('operation', '=', 'adopt')],
            order='id desc', limit=1)
        self.assertEqual(log.state, 'error')
        self.assertIn('2 orphan', log.message)

    def test_a_meeting_that_already_has_a_recording_is_untouched(self):
        arrived = self._meeting(950004, "Already has one", minutes_ago=0)
        arrived.sudo().with_context(sembly_sync=True).write(
            {'google_file_id': 'drive-existing'})
        self._orphan("Already has one", minutes_ago=0, file_id='drive-other')
        arrived.invalidate_recordset(['google_file_id'])
        self.assertEqual(arrived.google_file_id, 'drive-existing')

    def test_adoption_upgrades_a_translation_into_a_merge(self):
        """The notes were translated alone because Sembly had nothing to merge
        with. Now that its summary exists, the merge is the better answer."""
        orphan = self._orphan("Late summary", minutes_ago=0, file_id='drive-notes')
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>مترجم.</p>"):
            orphan._apply_gemini_notes("English notes from Gemini.")
        self.assertEqual(orphan.merged_summary_source, 'translated')

        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>مدموج.</p>") as ask:
            # Sembly's arrival must actually CARRY a summary, otherwise there is
            # nothing to merge with and not re-merging is the correct answer.
            arrived = self._meeting(950005, "Late summary", minutes_ago=0,
                                    with_summary=True)
        self.assertEqual(arrived.google_file_id, 'drive-notes')
        self.assertIn("ملخص Sembly", ask.call_args[0][0])
        self.assertEqual(arrived.merged_summary_source, 'merged')

    # ---------------------------------------------------- who may publish a link
    def _employee(self):
        return self.env['res.users'].sudo().create({
            'name': "Plain Employee", 'login': 'share-test-employee@era.net.sa',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

    def test_an_employee_cannot_publish_a_recording(self):
        """The button's groups= only hides it; an RPC call must still be refused
        (rule 19). Publishing opens the recording to whoever holds the link."""
        from odoo.exceptions import AccessError
        meeting = self._meeting(960001, "Share guard", minutes_ago=0)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'google_file_id': 'drive-guard'})
        with self.assertRaises(AccessError):
            meeting.with_user(self._employee()).action_google_share_link()

    def test_an_employee_cannot_revoke_one_either(self):
        from odoo.exceptions import AccessError
        meeting = self._meeting(960002, "Revoke guard", minutes_ago=0)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'google_file_id': 'drive-guard2'})
        with self.assertRaises(AccessError):
            meeting.with_user(self._employee()).action_google_revoke_link()

    def test_the_sales_manager_holds_the_right(self):
        """Granted by era_sembly_meetings_crm, which is the only module that may
        name that group — the base cannot, since it does not exist without CRM."""
        manager = self.env['res.users'].sudo().create({
            'name': "Sales Manager", 'login': 'share-test-sales@era.net.sa',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('sales_team.group_sale_manager').id])],
        })
        self.assertTrue(manager.has_group('era_sembly_meetings.group_sembly_share'))

    def test_the_sembly_manager_still_holds_it(self):
        manager = self.env['res.users'].sudo().create({
            'name': "Sembly Manager", 'login': 'share-test-sembly@era.net.sa',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('era_sembly_meetings.group_sembly_manager').id])],
        })
        self.assertTrue(manager.has_group('era_sembly_meetings.group_sembly_share'))

    def test_the_right_is_not_implied_by_being_an_employee(self):
        self.assertFalse(self._employee().has_group(
            'era_sembly_meetings.group_sembly_share'))

    # ------------------------------------------------------------ presentation
    def test_the_gemini_document_link_needs_no_public_sharing(self):
        """Offered as a plain Drive link: whoever follows it uses their own
        access, which is the right default for a document nobody published."""
        meeting = self._meeting(970001, "Notes link", minutes_ago=0)
        self.assertFalse(meeting.gemini_notes_url)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'google_notes_file_id': 'doc-xyz'})
        self.assertEqual(meeting.gemini_notes_url,
                         'https://docs.google.com/document/d/doc-xyz/edit')
        self.assertFalse(meeting.google_share_url,
                         "reading the notes must not require publishing anything")

    def test_the_summary_page_shows_ONE_text_with_the_originals_behind_buttons(self):
        """A reader wants the answer, not the working. The page renders a single
        final_summary; each original is one click away instead of stacked."""
        view = self.env['sembly.meeting'].get_view(
            self.env.ref('era_sembly_meetings.view_sembly_meeting_form').id, 'form')
        arch = view['arch']
        page = arch.index('name="summary"')
        self.assertIn('final_summary', arch)
        self.assertGreater(arch.index('final_summary'), page)
        for action in ('action_show_sembly_text', 'action_show_brief_text',
                       'action_show_gemini_text'):
            self.assertIn(action, arch, "%s must be reachable" % action)
        self.assertNotIn('name="google"', arch,
                         "the Google tab is gone; its links moved above the tabs")

    def test_the_chosen_text_is_the_merge_when_there_is_one(self):
        meeting = self._meeting(990001, "Which text wins", minutes_ago=0,
                                with_summary=True)
        meeting.sudo().with_context(sembly_sync=True).write({
            'gemini_notes': "<p>Gemini original.</p>",
            'gemini_notes_ar': "<p>النص المدموج.</p>",
            'merged_summary_source': 'merged',
        })
        self.assertIn("المدموج", meeting.final_summary)
        self.assertEqual(meeting.final_summary_label, "Sembly + Gemini (مدموج)")

    def test_with_one_provider_the_chosen_text_is_simply_that_one(self):
        meeting = self._orphan("Only google", minutes_ago=0, file_id='drive-only')
        meeting.sudo().with_context(sembly_sync=True).write(
            {'gemini_notes': "<p>Gemini alone.</p>"})
        self.assertIn("Gemini alone", meeting.final_summary)
        self.assertEqual(meeting.final_summary_label, "Gemini")
    def test_the_links_sit_above_the_tabs(self):
        view = self.env['sembly.meeting'].get_view(
            self.env.ref('era_sembly_meetings.view_sembly_meeting_form').id, 'form')
        arch = view['arch']
        self.assertLess(arch.index('google_recording_url'), arch.index('<notebook'),
                        "a link is something you reach for, not something you read")

    # ------------------------------------------- the summary chain end to end
    def test_a_google_only_meeting_can_still_be_summarised_and_posted(self):
        """Record 2163 in production: it had a full Arabic translation, yet
        has_summary was False, no executive brief was ever built and nothing
        could reach a linked record — because every summariser read the Sembly
        `summary` field alone."""
        meeting = self._orphan("Google only narrative", minutes_ago=0,
                               file_id='drive-narrative')
        self.assertFalse(meeting.summary)
        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<p>مترجم.</p>"):
            meeting._apply_gemini_notes("English notes from Gemini.")
        self.assertTrue(meeting.gemini_notes_ar)
        self.assertTrue(meeting.has_summary,
                        "a stored computed field must see the provider's narrative")
        self.assertTrue(meeting._narrative_sources())

    def test_the_executive_brief_is_built_from_the_MERGE_not_sembly_alone(self):
        """The merged text already contains both providers, so it REPLACES the
        raw Sembly summary as the brief's source rather than sitting beside it —
        otherwise the same material is fed twice."""
        meeting = self._meeting(980001, "Brief from merge", minutes_ago=0,
                                with_summary=True)
        meeting.sudo().with_context(sembly_sync=True).write({
            'gemini_notes_ar': "<p>النص المدموج من المصدرين.</p>",
            'merged_summary_source': 'merged',
        })
        labels = [label for label, _ in meeting._narrative_sources()]
        self.assertEqual(labels, ["Sembly + Gemini (مدموج)"])

        with patch.object(type(self.Meeting), '_ask_agent',
                          return_value="<h4>حالة العميل</h4>") as ask:
            meeting._ensure_ai_brief()
        prompt = ask.call_args[0][0]
        self.assertIn("النص المدموج", prompt,
                      "the brief must be built from the merged text")

    def test_an_untranslated_meeting_still_offers_geminis_own_words(self):
        meeting = self._orphan("Raw notes only", minutes_ago=0, file_id='drive-raw')
        meeting.sudo().with_context(sembly_sync=True).write(
            {'gemini_notes': "<p>Gemini wrote this.</p>"})
        labels = [label for label, _ in meeting._narrative_sources()]
        self.assertIn("Gemini", labels)
        self.assertTrue(meeting.has_summary)

    # --------------------------------------------------- links live in تقني
    def test_the_front_of_the_record_carries_buttons_not_raw_urls(self):
        """A 90-character Drive URL printed on the front costs two lines and
        tells a reader nothing they can act on — they want to open it."""
        view = self.env['sembly.meeting'].get_view(
            self.env.ref('era_sembly_meetings.view_sembly_meeting_form').id, 'form')
        arch = view['arch']
        notebook = arch.index('<notebook')
        buttons = arch.index('name="google_buttons"')
        self.assertLess(buttons, notebook, "the buttons belong above the tabs")

        # The FIELD declaration, not the first mention of the name — the
        # button's invisible="not google_recording_url" mentions it too, and
        # matching that measured the button instead of the field.
        technical = arch.index('name="technical"')
        self.assertGreater(arch.index('<field name="google_recording_url"'),
                           technical,
                           "the raw URL belongs with the other technical facts")

    def test_opening_a_recording_returns_a_url_action(self):
        meeting = self._meeting(996001, "Open me", minutes_ago=0)
        meeting.sudo().with_context(sembly_sync=True).write(
            {'google_recording_url': 'https://drive.google.com/file/d/x/view'})
        action = meeting.action_open_google_recording()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertEqual(action['target'], 'new')

    def test_opening_a_missing_link_says_why(self):
        """Better a clear message than a button that silently does nothing."""
        from odoo.exceptions import UserError
        meeting = self._meeting(996002, "Nothing to open", minutes_ago=0)
        with self.assertRaises(UserError):
            meeting.action_open_google_share()

    def test_the_crons_survive_a_module_upgrade(self):
        """A cron armed at runtime must not be disarmed by an unrelated deploy.

        Observed in production: the Gemini notes backfill was running, an
        unrelated change was deployed, the module upgraded, and Odoo re-applied
        active="False" from the data file — the job stopped silently at 53 of
        398 and nobody would have known without the monitor. noupdate on the
        record is what makes the runtime state stick.
        """
        for xmlid in ('cron_sembly_google_sync', 'cron_google_backfill',
                      'cron_google_notes_backfill'):
            record = self.env['ir.model.data'].search([
                ('module', '=', 'era_sembly_meetings_google'),
                ('name', '=', xmlid)], limit=1)
            self.assertTrue(record, xmlid)
            self.assertTrue(record.noupdate,
                            "%s must be noupdate or an upgrade resets it" % xmlid)

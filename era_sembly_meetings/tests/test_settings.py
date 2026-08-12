# -*- coding: utf-8 -*-
"""Settings → Sembly.

Two behaviours worth pinning down, because both are easy to break silently:
the AI agent is CHOSEN from a list rather than typed as a number, and the MCP
token is write-only.
"""
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'sembly')
class TestSemblySettings(TransactionCase):

    def setUp(self):
        super().setUp()
        self.icp = self.env['ir.config_parameter'].sudo()
        self.agent = self.env['ai.agent'].create({
            'name': "Sembly test agent",
            'partner_id': self.env['res.partner'].create(
                {'name': "Sembly test agent"}).id,
        })

    def _settings(self, **values):
        return self.env['res.config.settings'].create(values)

    # ------------------------------------------------------------------ agent
    def test_agent_is_picked_from_a_list_not_typed(self):
        """A Many2one, so the user chooses an agent instead of guessing its id."""
        field = self.env['res.config.settings']._fields['sembly_ai_agent_id']
        self.assertEqual(field.type, 'many2one')
        self.assertEqual(field.comodel_name, 'ai.agent')

    def test_choosing_an_agent_stores_its_id_in_the_parameter(self):
        """The model side reads sembly.ai_agent_id as an int, so the parameter
        must keep holding a plain id."""
        self._settings(sembly_ai_agent_id=self.agent.id).execute()
        self.assertEqual(self.icp.get_param('sembly.ai_agent_id'), str(self.agent.id))

    def test_the_chosen_agent_comes_back_on_the_settings_page(self):
        self._settings(sembly_ai_agent_id=self.agent.id).execute()
        self.assertEqual(
            self._settings().default_get(['sembly_ai_agent_id'])['sembly_ai_agent_id'],
            self.agent.id)

    def test_the_matcher_routes_through_the_chosen_agent(self):
        self._settings(sembly_ai_agent_id=self.agent.id).execute()
        with patch.object(type(self.agent), 'get_direct_response',
                          return_value="{}") as ask:
            self.env['sembly.meeting']._ask_agent("hello")
        ask.assert_called_once()

    def test_a_deleted_agent_reports_clearly_instead_of_falling_back(self):
        self.icp.set_param('sembly.ai_agent_id', '999999999')
        with self.assertRaises(UserError) as ctx:
            self.env['sembly.meeting']._ask_agent("hello")
        self.assertIn("Settings", str(ctx.exception))

    def test_a_deleted_agent_does_not_break_the_settings_page(self):
        """Odoo resolves a dangling id to False rather than raising, so the page
        still opens and the admin can pick a new agent."""
        self.icp.set_param('sembly.ai_agent_id', '999999999')
        self.assertFalse(
            self._settings().default_get(['sembly_ai_agent_id']).get('sembly_ai_agent_id'))

    # ------------------------------------------------------------------ token
    def test_the_mcp_token_is_write_only(self):
        """It must never be readable back through the ORM (rule 03)."""
        self.icp.set_param('sembly.mcp_token', 'super-secret-token-value')
        shown = self._settings().default_get(['sembly_mcp_token'])['sembly_mcp_token']
        self.assertNotIn('super-secret', shown or '')
        self.assertTrue(shown.startswith('•'))

    def test_saving_the_mask_unchanged_keeps_the_token(self):
        self.icp.set_param('sembly.mcp_token', 'super-secret-token-value')
        masked = self._settings().default_get(['sembly_mcp_token'])['sembly_mcp_token']
        self._settings(sembly_mcp_token=masked).execute()
        self.assertEqual(self.icp.get_param('sembly.mcp_token'), 'super-secret-token-value')

    def test_the_webhook_url_is_built_from_the_generated_token(self):
        # Computed, not a config_parameter, so it is read off the record rather
        # than out of default_get().
        url = self._settings().sembly_webhook_url
        self.assertIn('/sembly/webhook/', url)
        self.assertIn(self.icp.get_param('sembly.webhook_token'), url)

    # ------------------------------------------------------------- text dialogs
    def _meeting(self):
        from odoo.addons.era_sembly_meetings.tests import fixtures
        return self.env['sembly.meeting']._upsert_from_mcp(
            dict(fixtures.LIST_MEETINGS_META, id=995001))

    def test_a_text_dialog_opens_as_a_MODAL_not_a_page(self):
        """The bug this replaced: an act_window whose res_model and res_id are
        the record already on screen is not honoured as a dialog — Odoo replaced
        the whole page with it, menu bar and New button included."""
        meeting = self._meeting()
        meeting.sudo().write({'ai_brief': "<p>مختصر.</p>"})
        action = meeting.action_show_brief_text()
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['res_model'], 'sembly.text.dialog',
                         "it must NOT point back at sembly.meeting")
        self.assertNotEqual(action['res_model'], meeting._name)

    def test_the_dialog_carries_the_text_and_says_where_it_goes(self):
        meeting = self._meeting()
        meeting.sudo().write({'ai_brief': "<p>هذا هو المختصر.</p>"})
        action = meeting.action_show_brief_text()
        dialog = self.env['sembly.text.dialog'].browse(action['res_id'])
        self.assertIn("هذا هو المختصر", dialog.body)
        self.assertIn("شاتر", dialog.note)
        self.assertEqual(dialog.meeting_id, meeting)

    def test_an_empty_original_still_opens_and_says_so(self):
        """Better an honest empty dialog than a button that does nothing."""
        meeting = self._meeting()
        meeting.sudo().write({'summary': False})
        dialog = self.env['sembly.text.dialog'].browse(
            meeting.action_show_sembly_text()['res_id'])
        self.assertIn("لا يوجد", dialog.body)

    def test_the_front_sheet_keeps_only_what_a_reader_needs(self):
        """Participants sit in the SECOND column, not spanning the sheet — a
        dozen partner tags at full width pushed everything else off the first
        screen. Platform is provenance and belongs in تقني."""
        view = self.env['sembly.meeting'].get_view(
            self.env.ref('era_sembly_meetings.view_sembly_meeting_form').id, 'form')
        arch = view['arch']
        notebook = arch.index('<notebook')
        technical = arch.index('name="technical"')

        self.assertLess(arch.index('name="participants"'), notebook,
                        "participants stay above the tabs")
        self.assertGreater(arch.index('<field name="platform"'), technical,
                           "platform is provenance, so it lives in تقني")

    def test_the_historical_import_is_started_from_settings(self):
        """Both providers' history imports live in the same place: a
        whole-history import is a configuration decision taken once, not a
        per-use dialog, and side by side it is obvious that both exist."""
        self.icp.set_param('sembly.mcp_token', 'tok-for-tests')
        cron = self.env.ref('era_sembly_meetings.cron_sembly_backfill')
        cron.sudo().write({'active': False})

        self._settings().action_sembly_start_backfill()
        cron.invalidate_recordset(['active'])
        self.assertTrue(cron.active)
        self.assertEqual(self.icp.get_param('sembly.backfill_state'), 'running')

    def test_it_refuses_to_start_without_a_token(self):
        self.icp.set_param('sembly.mcp_token', '')
        with self.assertRaises(UserError):
            self._settings().action_sembly_start_backfill()

    def test_stopping_from_settings_disarms_the_cron(self):
        self.icp.set_param('sembly.mcp_token', 'tok-for-tests')
        cron = self.env.ref('era_sembly_meetings.cron_sembly_backfill')
        self._settings().action_sembly_start_backfill()
        self._settings().action_sembly_stop_backfill()
        cron.invalidate_recordset(['active'])
        self.assertFalse(cron.active)
        self.assertEqual(self.icp.get_param('sembly.backfill_state'), 'idle')

    # ------------------------------------------------------------- the layout
    def _form_arch(self):
        return self.env['sembly.meeting'].get_view(
            self.env.ref('era_sembly_meetings.view_sembly_meeting_form').id,
            'form')['arch']

    def test_the_facts_are_a_caption_not_a_grid(self):
        """A labelled group of three short lines is what made the right column
        end after three rows and sit empty for the rest of the sheet."""
        arch = self._form_arch()
        self.assertIn('name="meeting_facts"', arch)
        self.assertLess(arch.index('name="meeting_facts"'), arch.index('<notebook'))
        self.assertNotIn('string="الاجتماع"', arch,
                         "the three facts are a caption line now, not a group")

    def test_there_is_no_filler_cell_left(self):
        """The empty <group/> was literally the blank bottom-right quarter in
        the screenshot: two rows by two columns with nothing in the fourth.

        Checks the STRUCTURE, not the text — the comment that explains the old
        trick contains the string "<group/>" and fooled the first version of
        this test.
        """
        from lxml import etree
        root = etree.fromstring(self._form_arch())
        empties = [g for g in root.iter('group')
                   if not g.attrib and not len(g) and not (g.text or '').strip()]
        self.assertFalse(empties, "a group with no attributes and no children "
                                  "is a spacer, and spacers leave holes")

    def test_the_participant_cell_spans_its_column(self):
        """Without colspan the cell lands in Odoo's fit-content(150px) track and
        every long Arabic tag takes a line of its own."""
        arch = self._form_arch()
        tags = arch.index('name="partner_ids"')
        self.assertIn('colspan="2"', arch[tags - 200:tags + 200])

    def test_the_reading_panes_are_viewport_sized(self):
        """200px is a keyhole for a 36,000-character summary."""
        arch = self._form_arch()
        self.assertIn('max-height:60vh', arch)
        self.assertNotIn('max-height:200px', arch)

    def test_the_anchors_all_survive(self):
        """Four modules xpath onto these by name; a missing one is a hard
        ValidationError at their install, not a silent no-op."""
        arch = self._form_arch()
        for anchor in ('button_box', 'sembly_link_fields', 'provider_links',
                       'participants', 'provider_technical'):
            self.assertIn('name="%s"' % anchor, arch, anchor)

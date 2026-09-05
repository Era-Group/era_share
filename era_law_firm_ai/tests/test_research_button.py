"""The research button opens research, and leaves Odoo's own button alone.

Which agent a button opens is a data decision here — an ai.composer row maps
the button's interface key to the agent — so these are the claims worth
holding: the mapping exists and points at the research agent, that agent is
still the one pinned to the statute corpus, and nothing about Odoo's general
assistant moved.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_law_firm_ai.models.ai_research_button import RESEARCH_KEY


@tagged('post_install', '-at_install')
class TestResearchButton(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.composer = cls.env.ref('era_law_firm_ai.composer_legal_research')

    def test_the_button_opens_the_research_agent(self):
        self.assertEqual(self.composer.interface_key, RESEARCH_KEY)
        self.assertEqual(self.composer.ai_agent,
                         self.env.ref('era_law_firm_ai.agent_research'))

    def test_that_agent_still_answers_only_from_the_corpus(self):
        """The whole point of a separate button: this agent is not a general
        assistant, and would be a poor one."""
        self.assertTrue(self.composer.ai_agent.restrict_to_sources)

    def test_the_key_is_a_real_selection_value(self):
        """A composer row whose key is not in the selection would be
        unreachable, and would fail validation on the next write."""
        self.assertIn(RESEARCH_KEY,
                      dict(self.env['ai.composer']._fields['interface_key'].selection))

    def test_odoo_s_own_ai_button_is_untouched(self):
        """The systray composer is the database's, shared by every app."""
        general = self.env['ai.composer'].search([
            ('interface_key', '=', 'systray_ai_button'),
            ('focused_models', '=', False)], limit=1)
        self.assertTrue(general, 'precondition: Odoo ships one')
        self.assertNotEqual(general.ai_agent,
                            self.env.ref('era_law_firm_ai.agent_research'))

    def test_the_record_button_answers_with_the_case_assistant(self):
        """The two buttons carry different things, so they get different agents.

        Odoo's own button passes the record it is looking at; the research
        agent is restricted to the statute corpus and will not read a record it
        is handed, so on a case file it could only ever answer questions about
        something else."""
        record = self.env.ref('era_law_firm_ai.composer_legal_record')
        self.assertEqual(record.interface_key, 'chatter_ai_button')
        self.assertEqual(record.ai_agent,
                         self.env.ref('era_law_firm_ai.agent_case_assistant'))
        self.assertFalse(record.ai_agent.restrict_to_sources,
                         'it has to be able to use the record')
        covered = record.focused_models.mapped('model')
        for model in ('legal.case', 'legal.hearing', 'legal.deadline', 'legal.document'):
            self.assertIn(model, covered)
        self.assertTrue(record.default_prompt)
        self.assertGreaterEqual(len(record.available_prompts), 3)

    def test_the_two_buttons_do_not_open_the_same_agent(self):
        """If they did, one of them would be redundant."""
        record = self.env.ref('era_law_firm_ai.composer_legal_record')
        self.assertNotEqual(record.ai_agent, self.composer.ai_agent)

    def test_it_offers_questions_to_start_from(self):
        prompts = self.composer.available_prompts
        self.assertGreaterEqual(len(prompts), 3)
        for prompt in prompts:
            self.assertTrue(any('؀' <= c <= 'ۿ' for c in prompt.name),
                            'the lawyers reading these read Arabic: %s' % prompt.name)

    def test_a_firm_can_point_the_button_elsewhere(self):
        """It is a data decision, not one baked into the interface: the record
        is editable, and not marked as a system default."""
        self.assertFalse(self.composer.is_system_default)
        self.composer.ai_agent = self.env.ref('era_law_firm_ai.agent_case_assistant')
        self.assertEqual(self.composer.ai_agent.name,
                         self.env.ref('era_law_firm_ai.agent_case_assistant').name)

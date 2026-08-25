"""The governance layer in front of Odoo's own AI agent."""

from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAIAgentBridge(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids = [
            (4, cls.env.ref('era_law_firm.group_legal_manager').id),
            (4, cls.env.ref('era_law_firm_ai.group_legal_ai_user').id)]
        cls.env.company.legal_ai_enabled = True
        cls.agent = cls.env.ref('era_law_firm_ai.agent_drafting')
        cls.agent.write({
            'legal_processing_location': 'KSA',
            'legal_retention_policy': 'Not retained.',
            'legal_approved': True,
        })

    def _request(self, **overrides):
        values = {
            'agent_id': self.agent.id,
            'purpose': 'مسودة مذكرة جوابية',
            'field_ids': [(6, 0, self.env.ref('era_law_firm_ai.field_case_name').ids)],
            'input_payload': 'الموكل 1012345678 والهاتف 0551234567 والبريد a@example.com',
        }
        values.update(overrides)
        return self.env['legal.ai.request'].create(values)

    def test_the_shipped_agents_exist_with_prompts(self):
        for xmlid in ('agent_contract_review', 'agent_drafting', 'agent_summary', 'agent_research'):
            agent = self.env.ref(f'era_law_firm_ai.{xmlid}')
            self.assertTrue(agent.system_prompt, f'{xmlid} has no system prompt')
            self.assertIn('محام', agent.system_prompt, 'the prompt should place a lawyer in the loop')

    def test_the_research_agent_is_pinned_to_its_sources(self):
        """It cites statute, so it must not answer from the model's own memory."""
        self.assertTrue(self.env.ref('era_law_firm_ai.agent_research').restrict_to_sources)

    def test_a_new_agent_is_not_approved_out_of_the_box(self):
        """Approving is the firm's decision, not something the module makes for it."""
        agent = self.env['ai.agent'].create({'name': 'Fresh Agent'})
        self.assertFalse(agent.legal_approved)
        self.assertEqual(agent.legal_max_classification, 'confidential')

    def test_an_agent_cannot_be_approved_without_its_pdpl_record(self):
        agent = self.env['ai.agent'].create({'name': 'Undocumented Agent'})
        with self.assertRaises(ValidationError):
            agent.legal_approved = True

    def test_an_unapproved_agent_receives_nothing(self):
        self.agent.legal_approved = False
        request = self._request()
        request.action_approve()
        calls = []

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            calls.append(prompt)
            return ['x']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            with self.assertRaises(UserError):
                request.action_send()
        self.assertFalse(calls)

    def test_the_agent_only_ever_sees_the_redacted_payload(self):
        request = self._request()
        request.action_approve()
        captured = {}

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            captured['prompt'] = prompt
            return ['مسودة المذكرة.']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            request.action_send()

        self.assertIn('prompt', captured, 'the agent was never called')
        self.assertNotIn('1012345678', captured['prompt'])
        self.assertNotIn('0551234567', captured['prompt'])
        self.assertNotIn('a@example.com', captured['prompt'])
        self.assertEqual(request.state, 'done')
        self.assertTrue(request.sanitized_response)
        self.assertFalse(request.input_payload, 'the original input must be discarded on dispatch')
        self.assertTrue(request.payload_hash)

    def test_consent_is_required_before_the_agent_is_reached(self):
        request = self._request()
        calls = []

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            calls.append(prompt)
            return ['x']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            with self.assertRaises(UserError):
                request.action_send()
        self.assertFalse(calls, 'nothing may reach the agent without consent')

    def test_a_disabled_company_reaches_no_agent(self):
        self.env.company.legal_ai_enabled = False
        request = self._request()
        request.action_approve()
        calls = []

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            calls.append(prompt)
            return ['x']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            with self.assertRaises(UserError):
                request.action_send()
        self.assertFalse(calls)

    def test_an_agent_failure_surfaces_instead_of_stranding_the_request(self):
        """The old HTTP transport left requests sitting in `sent` for ever."""
        request = self._request()
        request.action_approve()

        def failing(agent_self, prompt, chat_history=None, extra_system_context=""):
            raise ValueError('no API key configured')

        with patch.object(type(self.agent), '_generate_response', failing):
            with self.assertRaises(UserError):
                request.action_send()

    def test_the_form_explains_an_empty_agent_list(self):
        """An empty dropdown is the safeguard working; the form has to say so."""
        self.env['ai.agent'].sudo().search([]).write({'legal_approved': False})
        self.assertFalse(self.env['legal.ai.request'].new({}).has_approved_agent)

        self.agent.write({
            'legal_processing_location': 'KSA',
            'legal_retention_policy': 'Not retained.',
            'legal_approved': True,
        })
        self.assertTrue(self.env['legal.ai.request'].new({}).has_approved_agent)

    def test_only_approved_agents_are_offered(self):
        unapproved = self.env['ai.agent'].create({'name': 'Not Approved Yet'})
        offered = self.env['ai.agent'].search([('legal_approved', '=', True)])
        self.assertIn(self.agent, offered)
        self.assertNotIn(unapproved, offered)

    def test_each_precondition_reports_itself(self):
        """One shared message meant a shut kill switch read like missing consent."""
        request = self._request()
        request.action_approve()

        self.env.company.legal_ai_enabled = False
        with self.assertRaises(UserError) as caught:
            request._assert_gate_open()
        self.assertIn('switched off', str(caught.exception))

        self.env.company.legal_ai_enabled = True
        self.agent.legal_approved = False
        with self.assertRaises(UserError) as caught:
            request._assert_gate_open()
        self.assertIn('not approved', str(caught.exception))

        self.agent.legal_approved = True
        request.write({'consent_user_id': False, 'consent_date': False})
        with self.assertRaises(UserError) as caught:
            request._assert_gate_open()
        self.assertIn('consent', str(caught.exception))

    def test_the_gate_opens_once_everything_is_in_place(self):
        request = self._request()
        request.action_approve()
        self.env.company.legal_ai_enabled = True
        request._assert_gate_open()

    def test_the_response_is_rendered_as_html(self):
        """Models answer in markdown; stored flat it arrives as one unreadable block."""
        request = self._request()
        request.action_approve()

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            return ['## ملخص\n\nالعقد **صحيح**.\n\n1. أولاً\n2. ثانياً']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            request.action_send()

        body = request.sanitized_response
        self.assertIn('<h2', body)
        self.assertIn('<strong', body)
        self.assertIn('<ol', body)

    def test_script_in_a_response_does_not_survive(self):
        """The content comes from a language model and is not trusted."""
        request = self._request()
        request.action_approve()

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            return ['نص عادي <script>alert(1)</script><img src=x onerror=alert(2)>']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            request.action_send()

        body = request.sanitized_response or ''
        self.assertNotIn('<script', body)
        self.assertNotIn('onerror', body)

    def test_redaction_runs_before_rendering(self):
        request = self._request()
        request.action_approve()

        def fake_generate(agent_self, prompt, chat_history=None, extra_system_context=""):
            return ['**تواصل**: 0551234567 وهوية 1012345678']

        with patch.object(type(self.agent), '_generate_response', fake_generate):
            request.action_send()

        body = request.sanitized_response
        self.assertNotIn('0551234567', body)
        self.assertNotIn('1012345678', body)
        self.assertIn('<strong', body)

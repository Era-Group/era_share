"""The research button opens research, and leaves Odoo's own button alone.

Which agent a button opens is a data decision here — an ai.composer row maps
the button's interface key to the agent — so these are the claims worth
holding: the mapping exists and points at the research agent, that agent is
still the one pinned to the statute corpus, and nothing about Odoo's general
assistant moved.
"""
from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_law_firm_ai.models.ai_research_button import RESEARCH_KEY


@tagged('post_install', '-at_install')
class TestResearchButton(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.composer = cls.env.ref('era_law_firm_ai.composer_legal_research')

    def test_the_button_opens_an_agent_that_carries_the_corpus(self):
        """The point of a button of our own: Odoo's shared agent has no legal
        sources, and giving it ours would put statutes into every other app."""
        self.assertEqual(self.composer.interface_key, RESEARCH_KEY)
        self.assertEqual(self.composer.ai_agent,
                         self.env.ref('era_law_firm_ai.agent_legal_advisor'))
        self.assertTrue(self.composer.ai_agent.moj_corpus_target)

    def test_that_agent_is_not_locked_out_of_the_record(self):
        """It is given the open file as well as the statutes, and an agent
        restricted to its sources refuses to read what it was handed."""
        self.assertFalse(self.composer.ai_agent.restrict_to_sources)

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
                            self.env.ref('era_law_firm_ai.agent_legal_advisor'))

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


@tagged('post_install', '-at_install')
class TestAskAIFromRecord(TransactionCase):
    """Hiding Odoo's button must not cost the lawyer the way in.

    Odoo's AI button is hidden inside this app because it sends a record and
    its chatter with no consent, redaction or audit. What replaces it has to
    reach the same places — a hearing, a deadline, a document — and land on
    the wizard that does keep that chain.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي المدخل', 'login': 'ask_from_record',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})
        client = cls.env['res.partner'].create({'name': 'موكّل المدخل'})
        wizard = cls.env['legal.intake.wizard'].with_user(cls.lawyer).create({
            'client_id': client.id, 'case_type': 'litigation',
            'lawyer_id': cls.lawyer.id, 'engagement_type': 'none'})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def _hearing(self):
        now = fields.Datetime.now()
        return self.env['legal.hearing'].create({
            'name': 'جلسة المدخل', 'case_id': self.case.id,
            'lawyer_id': self.lawyer.id, 'start_datetime': now,
            'stop_datetime': now + timedelta(hours=1),
            'company_id': self.env.company.id})

    def test_a_hearing_opens_the_governed_wizard_on_its_case(self):
        action = self._hearing().action_ask_ai()
        self.assertEqual(action['res_model'], 'legal.ai.playbook.wizard')
        self.assertEqual(action['context']['default_case_id'], self.case.id)

    def test_a_document_arrives_with_itself_already_chosen(self):
        attachment = self.env['ir.attachment'].create({
            'name': 'ورقة.txt', 'raw': b'x', 'mimetype': 'text/plain',
            'res_model': 'legal.case', 'res_id': self.case.id})
        document = self.env['legal.document'].create({
            'name': 'ورقة الملف', 'case_id': self.case.id,
            'attachment_id': attachment.id, 'owner_id': self.lawyer.id})
        action = document.action_ask_ai()
        self.assertEqual(action['context']['default_case_id'], self.case.id)
        self.assertEqual(action['context']['default_document_id'], document.id,
                         'a task that works on a document should not ask which')

    def test_every_record_of_a_file_offers_it(self):
        for model in ('legal.hearing', 'legal.deadline', 'legal.document',
                      'legal.consultation', 'legal.engagement',
                      'legal.conflict.check'):
            self.assertTrue(hasattr(self.env[model], 'action_ask_ai'), model)

    def test_a_record_with_no_case_says_so(self):
        """A consultation can exist before there is a case, and every AI task
        works on one — so the button has to say that rather than open a wizard
        it cannot fill."""
        from odoo.exceptions import UserError
        consultation = self.env['legal.consultation'].create({
            'name': 'استشارة قبل الملف',
            'partner_id': self.env['res.partner'].create({'name': 'مستشير'}).id,
            'lawyer_id': self.lawyer.id,
            'company_id': self.env.company.id})
        self.assertFalse(consultation.case_id, 'precondition')
        with self.assertRaises(UserError):
            consultation.action_ask_ai()


@tagged('post_install', '-at_install')
class TestAdvisorSeesTheFile(TransactionCase):
    """The firm's own button carries both halves: the statutes and the file.

    Odoo builds a record's context only for its own interface keys, so a button
    with a key of its own arrives with the record's id and nothing in it. The
    server fills that in — and only for this key, so the other buttons keep
    behaving as Odoo wrote them.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.advisor = cls.env.ref('era_law_firm_ai.agent_legal_advisor')
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي المستشار', 'login': 'advisor_lawyer',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})
        client = cls.env['res.partner'].create({'name': 'موكّل المستشار'})
        wizard = cls.env['legal.intake.wizard'].with_user(cls.lawyer).create({
            'client_id': client.id, 'case_type': 'litigation',
            'lawyer_id': cls.lawyer.id, 'engagement_type': 'none'})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])
        now = fields.Datetime.now()
        cls.hearing = cls.env['legal.hearing'].create({
            'name': 'جلسة المستشار', 'case_id': cls.case.id,
            'lawyer_id': cls.lawyer.id, 'start_datetime': now,
            'stop_datetime': now + timedelta(hours=1), 'hijri_date': '1447/10/03',
            'outcome': 'أمهلت المحكمة المدعى عليه أسبوعين',
            'company_id': cls.env.company.id})

    def test_the_advisor_can_read_both_the_statutes_and_the_record(self):
        self.assertTrue(self.advisor.moj_corpus_target, 'it needs the corpus')
        self.assertFalse(self.advisor.restrict_to_sources,
                         'restricted to its sources, it would refuse the record it was handed')
        self.assertEqual(
            self.env.ref('era_law_firm_ai.composer_legal_research').ai_agent,
            self.advisor, 'and it is what the button opens')

    def test_a_case_hands_over_its_file(self):
        text = '\n'.join(self.case._ai_initialise_context('era_legal_research'))
        for block in ('ملف القضية', 'سجل الجلسات', 'مسار الملف'):
            self.assertIn(block, text)
        self.assertIn('جلسة المستشار', text)
        self.assertIn('أمهلت المحكمة', text, 'what happened is the point of the log')

    def test_a_hearing_hands_over_the_file_it_belongs_to(self):
        text = '\n'.join(self.hearing._ai_initialise_context('era_legal_research'))
        self.assertIn('ملف القضية', text)
        self.assertIn(self.case.name, text)

    def test_the_other_buttons_are_left_as_odoo_wrote_them(self):
        ours = self.case._ai_initialise_context('era_legal_research')
        theirs = self.case._ai_initialise_context('chatter_ai_button')
        self.assertNotEqual(ours, theirs)
        self.assertFalse([line for line in theirs if 'ملف القضية' in line],
                         'our context belongs to our key alone')

    def test_the_backfill_does_not_run_twice(self):
        """It is called on every unchanged sync, so a second pass must be a
        no-op rather than a second copy of the corpus."""
        self.assertEqual(self.env['moj.law']._backfill_target_agents(), 0)

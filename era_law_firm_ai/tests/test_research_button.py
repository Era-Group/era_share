"""One AI button, and the agent behind it follows where the lawyer is.

There is a single button in the systray, Odoo's. Inside this app it announces
the firm's own interface key, and outside it announces Odoo's — which agent
each of those opens is a data decision, an ai.composer row per key. So these
are the claims worth holding: both keys reach the advisor, that agent is the
one pinned to the statute corpus, one of our records is answered by it even
when it is opened from outside the app, and Odoo's general assistant is
untouched everywhere else in the database.
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
        """The point of a key of our own: Odoo's shared agent has no legal
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

    def test_our_records_are_answered_by_the_advisor_from_anywhere(self):
        """A case followed from an invoice or from Discuss is still a case.

        Outside the app the button announces Odoo's key, so the composer on
        that key is what keeps one of our records from being answered by the
        general assistant."""
        record = self.env.ref('era_law_firm_ai.composer_legal_record')
        self.assertEqual(record.interface_key, 'chatter_ai_button')
        self.assertEqual(record.ai_agent,
                         self.env.ref('era_law_firm_ai.agent_legal_advisor'))
        self.assertFalse(record.ai_agent.restrict_to_sources,
                         'it has to be able to use the record')
        covered = record.focused_models.mapped('model')
        for model in ('legal.case', 'legal.hearing', 'legal.deadline', 'legal.document'):
            self.assertIn(model, covered)
        self.assertTrue(record.default_prompt)
        self.assertGreaterEqual(len(record.available_prompts), 3)

    def test_both_keys_speak_with_one_voice(self):
        """Whichever way in a lawyer takes, the same agent answers."""
        record = self.env.ref('era_law_firm_ai.composer_legal_record')
        self.assertEqual(record.ai_agent, self.composer.ai_agent)

    def test_the_case_assistant_keeps_the_work_its_name_describes(self):
        """It is the agent behind the tasks, where each task is named and the
        agent behind it never has to be."""
        assistant = self.env.ref('era_law_firm_ai.agent_case_assistant')
        tasks = self.env['legal.ai.playbook'].search([('agent_id', '=', assistant.id)])
        self.assertGreaterEqual(len(tasks), 10)

    def test_it_offers_questions_to_start_from(self):
        """In both flavours: the key covers every screen in the app, so a
        lawyer meets it on a case as often as on an empty search."""
        prompts = self.composer.available_prompts
        self.assertGreaterEqual(len(prompts), 6)
        names = prompts.mapped('name')
        self.assertIn('لخّص الملف المفتوح في خمسة أسطر', names, 'a record is open')
        self.assertIn('ما مدة الاعتراض على الحكم وما الذي تسري منه؟', names,
                      'and often none is')
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
    """The governed path has to reach as far as the quick one.

    The button in the systray answers from a hearing, a deadline or a document
    with no consent, redaction or audit — that is what makes it quick. The
    lawyer who needs an answer they can be asked about must not have to walk
    back to the case for it, so the governed wizard is offered on those records
    too, already pointed at the file they belong to.
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
    """The button carries both halves: the statutes and the open file.

    Odoo serialises a record for its own key and knows nothing of a case file;
    for ours it serialises nothing at all. The server fills in both — and only
    for the two keys that reach the advisor, so every other way of opening a
    chat behaves as Odoo wrote it.
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

    def test_odoo_s_key_carries_the_file_too(self):
        """It is the way in from outside the app, and the same agent answers
        it — so it cannot arrive with less of the file than ours does."""
        text = '\n'.join(self.case._ai_initialise_context('chatter_ai_button'))
        self.assertIn('ملف القضية', text)
        self.assertIn('جلسة المستشار', text)

    def test_any_record_in_the_app_hands_over_its_own_data(self):
        """The key covers every screen in the app, not a list of models: a
        statute, a rule, a template. Odoo serialises the record for its own
        key only, so for ours the server has to."""
        law = self.env['moj.law'].create({
            'name': 'نظام المرافعات الشرعية', 'law_id': 'test-procedures'})
        text = '\n'.join(law._ai_initialise_context('era_legal_research'))
        self.assertIn('test-procedures', text)
        self.assertIn('نظام المرافعات الشرعية', text,
                      'and in Arabic, not escaped into six characters a letter')

    def test_the_ways_in_we_do_not_own_are_left_alone(self):
        """A chat opened from a rich text field or from the systray outside
        the app is Odoo's, and must read exactly as Odoo wrote it."""
        for key in ('html_field_text_select', 'systray_ai_button', 'mail_composer'):
            text = '\n'.join(self.case._ai_initialise_context(key))
            self.assertNotIn('ملف القضية', text, key)

    def test_the_backfill_does_not_run_twice(self):
        """It is called on every unchanged sync, so a second pass must be a
        no-op rather than a second copy of the corpus."""
        self.assertEqual(self.env['moj.law']._backfill_target_agents(), 0)

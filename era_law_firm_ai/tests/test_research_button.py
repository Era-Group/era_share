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
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
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

    def test_its_questions_are_all_about_the_open_file(self):
        """This agent's job is the file in front of the lawyer, so that is what
        it offers to ask. The general statutory question has a door of its
        own, and its questions live there."""
        prompts = self.composer.available_prompts
        self.assertGreaterEqual(len(prompts), 5)
        for prompt in prompts:
            self.assertTrue(any(word in prompt.name for word in ('الملف', 'الجلسة', 'موقفنا')),
                            'not about the open file: %s' % prompt.name)
        for prompt in prompts:
            self.assertTrue(any('؀' <= c <= 'ۿ' for c in prompt.name),
                            'the lawyers reading these read Arabic: %s' % prompt.name)
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


@tagged('post_install', '-at_install')
class TestLegalResearchEntry(TransactionCase):
    """The reference the lawyer can reach without a case in front of them.

    The systray button answers with the advisor, which reads the open file and
    keeps answering where the corpus is silent. A lawyer looking up a rule
    wants the other promise: the article, or a plain "not in the sources". That
    is the research agent, and before this it had no door of its own — it
    answered one task inside the governed wizard and nothing else.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.action = cls.env.ref('era_law_firm_ai.action_legal_research')
        cls.composer = cls.env.ref('era_law_firm_ai.composer_legal_corpus')
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي البحث', 'login': 'research_entry',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})

    def test_it_opens_the_agent_that_cannot_answer_from_memory(self):
        """The whole point of a second door: this one is restricted to the
        corpus, so what comes back is the text or nothing."""
        agent = self.env.ref('era_law_firm_ai.agent_research')
        self.assertTrue(agent.restrict_to_sources)
        self.assertEqual(self.composer.ai_agent, agent)
        self.assertNotEqual(self.composer.ai_agent,
                            self.env.ref('era_law_firm_ai.agent_legal_advisor'))

    def test_a_lawyer_reaches_it_by_name(self):
        """It sits in the app's own AI section, not in the AI app's
        configuration, which is where the agents themselves are."""
        self.assertEqual(self.action.type, 'ir.actions.client')
        self.assertEqual(self.action.tag, 'era_law_firm_ai.legal_research_chat')
        menu = self.env.ref('era_law_firm_ai.menu_legal_research_chat')
        self.assertEqual(menu.parent_id, self.env.ref('era_law_firm_ai.menu_legal_ai_root'))
        visible = self.env['ir.ui.menu'].with_user(self.lawyer).search([('id', '=', menu.id)])
        self.assertTrue(visible, 'a lawyer has to be able to see it')

    def test_the_home_screen_offers_it_as_well(self):
        """The menu is where you look for it once you know it exists; the
        dashboard is where you meet it."""
        arch = self.env['legal.dashboard'].with_user(self.lawyer).get_view()['arch']
        self.assertIn(str(self.action.id), arch)

    def test_the_questions_it_offers_are_the_ones_asked_of_a_book(self):
        prompts = self.composer.available_prompts
        self.assertGreaterEqual(len(prompts), 5)
        for prompt in prompts:
            self.assertNotIn('هذا الملف', prompt.name, 'no file is open here')

    def test_a_chat_opened_here_carries_no_record(self):
        """A reference that reads whatever file you happen to have open is no
        longer a reference."""
        channel = self.env['discuss.channel'].with_user(self.lawyer).create_ai_draft_channel(
            'era_legal_corpus')
        text = '\n'.join(self.env['discuss.channel'].sudo().browse(
            channel['ai_channel_id']).ai_env_context or [])
        self.assertNotIn('ملف القضية', text)
        self.assertIn('بحث نظامي', text)


@tagged('post_install', '-at_install')
class TestPromptsFitTheSituation(TransactionCase):
    """The advisor's questions are about the file — when there is a file.

    Its button is in the systray, and the systray is on every screen. Pressed
    from the dashboard, "summarise the open file" is an offer about nothing, so
    the general questions are handed over instead — the reference's own list,
    kept in one place rather than written twice.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lawyer = cls.env['res.users'].create({
            'name': 'محامي الأسئلة', 'login': 'prompt_situation',
            'group_ids': [(6, 0, [
                cls.env.ref('base.group_user').id,
                cls.env.ref('era_law_firm.group_legal_lawyer').id,
                cls.env.ref('era_law_firm_ai.group_legal_ai_user').id])]})
        client = cls.env['res.partner'].create({'name': 'موكّل الأسئلة'})
        wizard = cls.env['legal.intake.wizard'].with_user(cls.lawyer).create({
            'client_id': client.id, 'case_type': 'litigation',
            'lawyer_id': cls.lawyer.id, 'engagement_type': 'none'})
        cls.case = cls.env['legal.case'].browse(wizard.action_open_case()['res_id'])

    def _prompts(self, **kwargs):
        channel = self.env['discuss.channel'].with_user(self.lawyer)
        return channel.create_ai_draft_channel('era_legal_research', **kwargs)['prompts']

    def test_a_plain_lawyer_gets_a_chat_at_all(self):
        """It opened for an administrator and raised for a lawyer: the file is
        rendered as the reader — which is what keeps another lawyer's
        restricted document out of it — and one of its blocks reads a model
        that only the accountant may."""
        channel = self.env['discuss.channel'].with_user(self.lawyer).create_ai_draft_channel(
            'era_legal_research', record_model='legal.case', record_id=self.case.id)
        text = '\n'.join(self.env['discuss.channel'].sudo().browse(
            channel['ai_channel_id']).ai_env_context or [])
        self.assertIn('ملف القضية', text)

    def test_a_block_the_reader_may_not_see_is_left_out_not_raised(self):
        """Losing the money is the right failure; losing the chat is not."""
        def refuse(self):
            raise AccessError('the accountant\'s business')

        with patch.object(type(self.case), '_era_render_financials', refuse):
            text = self.case._era_ai_file_context()
        self.assertIn('ملف القضية', text)
        self.assertNotIn('الملخص المالي', text)

    def test_on_a_record_it_asks_about_the_record(self):
        own = self.env.ref('era_law_firm_ai.composer_legal_research').available_prompts
        offered = self._prompts(record_model='legal.case', record_id=self.case.id)
        self.assertTrue(offered)
        for name in offered:
            self.assertIn(name, own.mapped('name'), 'a file is open: ask about it')

    def test_with_no_record_it_asks_what_a_book_is_asked(self):
        general = self.env.ref('era_law_firm_ai.composer_legal_corpus').available_prompts
        offered = self._prompts()
        self.assertTrue(offered)
        for name in offered:
            self.assertIn(name, general.mapped('name'),
                          'nothing about an open file when none is open')

    def test_odoo_s_own_key_always_has_a_record_so_it_keeps_its_own(self):
        """It only ever fires on a form, so its questions stay record-shaped."""
        record = self.env.ref('era_law_firm_ai.composer_legal_record')
        for prompt in record.available_prompts:
            self.assertTrue(any(word in prompt.name for word in ('الملف', 'الجلسة', 'المستندات')),
                            prompt.name)

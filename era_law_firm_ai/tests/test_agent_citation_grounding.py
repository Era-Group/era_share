"""An agent asked for article numbers must have the articles to read.

A prompt demanding statutory citation from an agent with no sources gets the
numbers recalled from memory. A wrong article number in a filed memo is the
most expensive thing this module can produce, and nothing downstream catches
it — it looks exactly like a correct one.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCitationGrounding(TransactionCase):
    # The agents whose prompts ask for the statutory basis.
    CITING = ('era_law_firm_ai.agent_research',
              'era_law_firm_ai.agent_drafting',
              'era_law_firm_ai.agent_contract_review')

    def test_every_citing_agent_carries_the_corpus(self):
        for xmlid in self.CITING:
            agent = self.env.ref(xmlid)
            self.assertTrue(
                agent.moj_corpus_target,
                f"{agent.name} asks for article numbers; it needs the texts")

    def test_the_summariser_is_deliberately_left_ungrounded(self):
        """Its job is the document in front of it, not the law."""
        agent = self.env.ref('era_law_firm_ai.agent_summary')
        self.assertFalse(agent.moj_corpus_target)
        self.assertIn('لا تضف حكماً نظامياً من عندك', agent.system_prompt or '',
                      'and its prompt must keep saying so')

    def test_citation_is_bounded_to_the_attached_texts(self):
        for xmlid in ('era_law_firm_ai.agent_drafting',
                      'era_law_firm_ai.agent_contract_review'):
            prompt = self.env.ref(xmlid).system_prompt or ''
            self.assertIn('المرفقة بهذا الوكيل', prompt,
                          f'{xmlid}: article numbers must be bounded to the sources')

    def test_contract_review_admits_what_it_cannot_ground(self):
        """نظام الشركات and نظام العمل are not Ministry of Justice texts."""
        prompt = self.env.ref('era_law_firm_ai.agent_contract_review').system_prompt or ''
        self.assertIn('ليسا ضمنها', prompt,
                      'naming a statute the corpus lacks must not imply it is grounded')

    def test_the_corpus_really_lacks_them(self):
        """If either is ever added, the wording above becomes wrong."""
        names = self.env['moj.law'].search([]).mapped('name')
        if not names:
            self.skipTest('corpus not synced in this database')
        self.assertFalse([n for n in names if 'الشركات' in n])
        self.assertFalse([n for n in names if n.startswith('نظام العمل')])

    def test_turning_the_corpus_off_on_a_grounded_agent_is_refused(self):
        """The prompt and the sources are one decision, not two.

        Unticking the flag silently returns the agent to citing from memory,
        and a wrong article number looks exactly like a right one.
        """
        from odoo.exceptions import ValidationError
        agent = self.env.ref('era_law_firm_ai.agent_drafting')
        self.assertIn('المرفقة بهذا الوكيل', agent.system_prompt or '')
        agent.sources_ids.unlink()
        with self.assertRaises(ValidationError):
            agent.moj_corpus_target = False

    def test_an_ungrounded_prompt_may_switch_freely(self):
        """The guard is about a specific promise, not about the flag itself."""
        agent = self.env['ai.agent'].create({
            'name': 'وكيل بلا استشهاد', 'llm_model': 'custom_llm/custom',
            'system_prompt': 'لخّص ما يصلك دون ذكر مواد نظامية.',
        })
        agent.moj_corpus_target = True
        agent.moj_corpus_target = False
        self.assertFalse(agent.moj_corpus_target)

    def test_the_summariser_can_still_be_saved(self):
        """It never promises bounded citation, so nothing constrains it."""
        agent = self.env.ref('era_law_firm_ai.agent_summary')
        agent.system_prompt = (agent.system_prompt or '') + '\n- سطر إضافي.'
        self.assertFalse(agent.moj_corpus_target)

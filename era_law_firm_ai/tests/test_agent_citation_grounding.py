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

    def test_every_statute_the_prompt_names_is_in_the_corpus(self):
        """A prompt that names a statute promises the agent can cite it.

        نظام الشركات and نظام العمل were absent when this agent was first
        grounded, and the prompt said so. They are published now, so the
        caveat is gone — and this assertion is what keeps the prompt and the
        corpus from drifting apart again in either direction.
        """
        names = self.env['moj.law'].search([]).mapped('name')
        if not names:
            self.skipTest('corpus not synced in this database')
        prompt = self.env.ref('era_law_firm_ai.agent_contract_review').system_prompt or ''
        for statute in ('نظام المعاملات المدنية', 'نظام الشركات', 'نظام العمل'):
            self.assertIn(statute, prompt, 'precondition: the prompt names it')
            self.assertTrue(
                [n for n in names if n.strip() == statute],
                f"the prompt names {statute} but the corpus does not carry it, "
                f"so its article numbers would come from memory")

    def test_the_caveat_about_missing_statutes_is_gone(self):
        prompt = self.env.ref('era_law_firm_ai.agent_contract_review').system_prompt or ''
        self.assertNotIn('ليسا ضمنها', prompt,
                         'both statutes are in the corpus now; the caveat is false')

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

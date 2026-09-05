"""Starter questions that fit the situation, not only the agent.

The advisor's questions are all about the open file, because that is what the
advisor is for. But its button is in the systray, and the systray is on every
screen: press it from the dashboard or a list and «summarise the open file»
is an offer about nothing.

Rather than write a second set of general questions for that case — the same
questions in two places, drifting apart — the composer with no record borrows
the statute reference's list, which is the one place the firm's general
questions are kept.
"""
import random

from odoo import api, models

from .ai_research_button import CORPUS_KEY, RESEARCH_KEY


class LegalChatPrompts(models.Model):
    _inherit = 'discuss.channel'

    @api.model
    def create_ai_draft_channel(self, caller_component, channel_title=None,
                                record_model=None, record_id=None,
                                front_end_info=None, text_selection=None):
        result = super().create_ai_draft_channel(
            caller_component, channel_title, record_model, record_id,
            front_end_info, text_selection)
        if caller_component != RESEARCH_KEY or record_model:
            return result
        corpus = self.env.ref('era_law_firm_ai.composer_legal_corpus',
                              raise_if_not_found=False)
        if corpus and corpus.interface_key == CORPUS_KEY:
            # Sampled the way Odoo samples its own: three of them, so the
            # questions on offer change from one chat to the next.
            names = corpus.sudo().available_prompts.mapped('name')
            result['prompts'] = random.sample(names, min(3, len(names)))
        return result

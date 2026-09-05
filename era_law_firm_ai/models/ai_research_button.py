"""The two ways the law firm's own agents are reached from the interface.

Odoo's systray button belongs to the whole database. Inside this app it says a
key of ours instead of Odoo's, so an ai.composer of ours answers it and the
lawyer gets the firm's advisor — the corpus and the open file together. Outside
the app nothing changes.

The second key has no button at all: it is the statute reference, opened by
name from the app's menu and from the dashboard. It is a separate key because
it is a separate promise — the agent behind it is restricted to its sources, so
it answers with the article or says the sources do not cover it, which is what
a reference is for and what an assistant reading a case file cannot be.

Which agent each key opens is a data decision, not one in the interface code:
an ai.composer record maps the key to the agent, the standing instructions and
the starter questions. A firm can point either somewhere else without touching
a line of code.
"""
from odoo import fields, models

# Odoo's own keys live in ai/models/ai_composer.py; these are added rather than
# borrowed so the composers that answer for them cannot collide with theirs.
RESEARCH_KEY = 'era_legal_research'
CORPUS_KEY = 'era_legal_corpus'


class AIComposer(models.Model):
    _inherit = 'ai.composer'

    interface_key = fields.Selection(
        selection_add=[
            (RESEARCH_KEY, 'The AI button, inside the law firm app'),
            (CORPUS_KEY, 'Legal research, from the law firm menu'),
        ],
        ondelete={RESEARCH_KEY: 'cascade', CORPUS_KEY: 'cascade'})

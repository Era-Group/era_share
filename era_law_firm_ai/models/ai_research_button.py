"""A second AI button, for statutory research, inside the law firm app.

Odoo's own systray button asks a general assistant, and it belongs to the whole
database rather than to this app. Taking it over would have meant a lawyer
losing the general assistant on their own screens — and losing it to an agent
that answers only from the indexed statutes, so «summarise this record» would
come back with «there is nothing in the attached sources». One button, one
behaviour: this one is always research, always from the corpus.

Which agent it opens is a data decision, not a decision in the interface code:
the button names an interface key, and an ai.composer record maps that key to
the agent, the standing instructions and the starter prompts. A firm can point
it somewhere else without touching a line of code.
"""
from odoo import fields, models

# Odoo's own keys live in ai/models/ai_composer.py; this one is added rather
# than borrowed so the composer that answers for it cannot collide with theirs.
RESEARCH_KEY = 'era_legal_research'


class AIComposer(models.Model):
    _inherit = 'ai.composer'

    interface_key = fields.Selection(
        selection_add=[(RESEARCH_KEY, 'Legal research, from the law firm app')],
        ondelete={RESEARCH_KEY: 'cascade'})

"""A register of the legislation the office works from.

The prompt this replaces listed some seventy links to laws.moj.gov.sa and told the
agent to read them before answering. A model running inside Odoo cannot open a
URL, so that instruction has two possible outcomes and both are bad: it is ignored,
or the model behaves as though it had read them and produces article numbers from
memory. In legal work an invented citation is the worst failure available.

So the list lives here as data instead. Each entry records the statute and where
its official text is, and tracks whether that text has actually been attached to an
agent as a source -- because only then does the agent read the law rather than
recall it.
"""

from odoo import _, api, fields, models


class LegalLegislation(models.Model):
    _name = 'legal.legislation'
    _description = 'Saudi Legislation Reference'
    _order = 'sequence, name, id'

    name = fields.Char(required=True, translate=True,
                       help="The statute as it is cited. Rename the seeded entries to the official "
                            "title once you have it from the Ministry of Justice.")
    moj_id = fields.Char(string='MoJ Reference', index=True,
                         help="The identifier the Ministry of Justice uses in its legislation portal.")
    url = fields.Char(string='Official Text',
                      help="Where the authoritative text is published. Reference only -- an agent "
                           "cannot open it.")
    category = fields.Selection([
        ('procedure', 'Procedure and Litigation'),
        ('civil', 'Civil and Commercial'),
        ('criminal', 'Criminal'),
        ('personal_status', 'Personal Status'),
        ('labour', 'Labour'),
        ('other', 'Other'),
    ], default='other')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    notes = fields.Text()
    agent_ids = fields.Many2many(
        'ai.agent', 'ai_agent_legislation_rel', 'legislation_id', 'agent_id',
        string='Agents Relying On It')
    source_attached = fields.Boolean(
        string='Text Attached as a Source',
        help="Tick once the statute's text has been uploaded to an agent as a source. Until then "
             "the agent can only recall the law, not read it -- and recalled article numbers are "
             "the failure this register exists to prevent.")

    _moj_unique = models.Constraint('UNIQUE(moj_id)', 'This legislation is already registered.')

    @api.depends('name', 'moj_id')
    def _compute_display_name(self):
        for record in self:
            record.display_name = f'{record.name} ({record.moj_id})' if record.moj_id else record.name


class AIAgentLegislation(models.Model):
    _inherit = 'ai.agent'

    legal_legislation_ids = fields.Many2many(
        'legal.legislation', 'ai_agent_legislation_rel', 'agent_id', 'legislation_id',
        string='Legislation Relied On',
        help="What this agent is expected to work from. Listing a statute here does not put its "
             "text in front of the agent; for that, attach the text under Sources.")
    legal_sources_pending = fields.Integer(
        compute='_compute_legal_sources_pending', string='Statutes Without a Source',
        help="Listed statutes whose text has not been attached as a source yet.")

    @api.depends('legal_legislation_ids.source_attached')
    def _compute_legal_sources_pending(self):
        for agent in self:
            agent.legal_sources_pending = len(
                agent.legal_legislation_ids.filtered(lambda law: not law.source_attached))

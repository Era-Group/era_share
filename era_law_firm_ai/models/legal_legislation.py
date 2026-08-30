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
    # No per-statute link. They were deep links into the Ministry's portal that no
    # longer resolve, and they were never of any use to an agent, which cannot open
    # a URL at all. One portal is named once, on the charter, as the place a human
    # verifies against.
    portal_url = fields.Char(
        string='Official Portal', compute='_compute_portal_url',
        help="The single authority for Saudi legislation. Open it to find and verify a statute's "
             "text, then attach that text to the agent as a source.")

    @api.depends_context('company')
    def _compute_portal_url(self):
        portal = self.env['legal.ai.charter']._reference_portal(self.env.company)
        for record in self:
            record.portal_url = portal
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
        help="The one thing on this row that changes what an agent can do. Until the statute's "
             "text is uploaded to an agent as a source, the agent can only recall the law, and a "
             "recalled article number is the failure this register exists to prevent.")

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

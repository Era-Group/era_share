"""ERA GEO — AI crawler registry.

One record per known AI/LLM crawler. ``allowed`` decides whether it may
access the site; the choices are rendered into ``robots.txt`` (the
``website.robots`` template is extended in views/robots_templates.xml).

Blocking a *training* crawler (e.g. GPTBot, CCBot) keeps your content out of
model training sets; allowing a *search/answer* crawler (e.g. OAI-SearchBot,
PerplexityBot) lets your pages be cited in AI answers. The default seed
errs toward allowing answer/search bots and is fully admin-editable.
"""
from odoo import api, fields, models

PURPOSES = [
    ('search', 'Search / Answer'),   # cites live pages in AI answers
    ('training', 'Model Training'),  # ingests content into training sets
    ('both', 'Search & Training'),
    ('other', 'Other'),
]


class EraGeoAiCrawler(models.Model):
    _name = 'era.geo.ai.crawler'
    _description = 'AI Crawler (GEO)'
    _order = 'vendor, name'

    name = fields.Char(string='Name', required=True, translate=True)
    user_agent = fields.Char(
        string='User-Agent Token',
        required=True,
        help='The robots.txt User-agent token, e.g. "GPTBot".',
    )
    vendor = fields.Char(string='Vendor')
    purpose = fields.Selection(PURPOSES, string='Purpose', default='search')
    allowed = fields.Boolean(
        string='Allowed',
        default=True,
        help='Allow this crawler in robots.txt. Untick to emit '
             '"Disallow: /" for its User-agent.',
    )
    documentation_url = fields.Char(string='Docs URL')
    active = fields.Boolean(default=True)

    _user_agent_unique = models.Constraint(
        'UNIQUE(user_agent)',
        'Each AI crawler User-agent must be unique.',
    )

    @api.model
    def _robots_block(self):
        """Return the robots.txt text block for all active AI crawlers.

        One ``User-agent`` stanza per crawler, ``Allow: /`` or
        ``Disallow: /`` per the ``allowed`` flag. Returns '' when there are
        no crawlers, so the robots.txt template stays clean.
        """
        crawlers = self.sudo().search([], order='user_agent')
        if not crawlers:
            return ''
        lines = ['', '# AI crawlers — managed by ERA GEO']
        for c in crawlers:
            if not c.user_agent:
                continue
            lines.append('User-agent: %s' % c.user_agent.strip())
            lines.append('Allow: /' if c.allowed else 'Disallow: /')
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'

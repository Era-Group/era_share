"""ERA GEO — extend ``era.seo.mixin`` with two citation-ready fields.

For AI answer engines to *cite* a page, two signals matter beyond the usual
SEO meta:

  - ``geo_answer_summary`` — a short, quotable answer (1-2 sentences) that
    LLMs can lift verbatim into an answer.
  - ``geo_key_takeaways`` — a small bullet list (3-5 items) that LLMs can
    summarise as a list response.

Both are translatable, land on every host model that carries the mixin
(website.page, blog.post, era.content.block, era.blog.series / category /
author), feed ``/llms.txt`` when present, and join the AI ``Fill SEO``
field set when ``era_seo_ai`` is installed.
"""
from odoo import fields, models


class EraSeoMixin(models.AbstractModel):
    _inherit = 'era.seo.mixin'

    geo_answer_summary = fields.Text(
        string='GEO Answer Summary',
        translate=True,
        help='One or two short, quotable sentences answering the page\'s core '
             'question. AI engines (ChatGPT, Perplexity, Google AI Overviews) '
             'lift this verbatim when citing the page. Keep it <= 240 chars.',
    )
    geo_key_takeaways = fields.Html(
        string='GEO Key Takeaways',
        translate=True,
        sanitize=True,
        help='3-5 bullet takeaways as a small <ul>. Quotable summary block '
             'for AI engines. Shown near the top of the page when rendered.',
    )

    def _ai_fill_fields(self):
        """Append the GEO fields to the AI fill spec set, defensively.

        ``_ai_fill_fields`` is defined by ``era_seo_ai``. We may run without
        it installed, in which case ``super()`` won't have the method and we
        simply return the GEO specs (unused by anything else). When AI is
        installed, our specs ride alongside the core SEO ones.
        """
        parent = getattr(super(), '_ai_fill_fields', None)
        specs = list(parent()) if parent else []
        specs += [
            {'name': 'geo_answer_summary',
             'rule': '1-2 short sentences, plain text, <= 240 chars, '
                     'directly answering the page\'s core question — '
                     'written so an AI engine can quote it verbatim'},
            {'name': 'geo_key_takeaways',
             'rule': 'a small <ul> with 3-5 short <li> bullet takeaways. '
                     'Plain inline HTML only (no scripts/styles/images). '
                     'One sentence each, parallel structure'},
        ]
        return specs

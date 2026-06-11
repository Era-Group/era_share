"""ERA GEO — citability checks bolted onto the SEO audit run.

GEO readiness is about whether AI answer engines can *discover, extract, and
cite* the site. These checks ride the existing ``era.seo.audit.run`` so the
findings show up in the same dashboard:

  - geo_llms_txt_disabled    (site) — /llms.txt is off
  - geo_answer_bots_blocked  (site) — answer/search crawlers are blocked
  - geo_no_heading_structure (page) — substantial page with no H2/H3 to chunk

Site-level findings are attached to the homepage (``/``) so they fit the
per-page finding model and auto-resolve when fixed.
"""
from odoo import models

_TRUE = ('True', '1', 'true', 'yes', 'on')
_GEO_MIN_WORDS = 150          # only flag heading structure on non-trivial pages
_GEO_SUMMARY_MIN_WORDS = 300  # only flag missing answer summary on long pages
_GEO_FAQ_MIN_WORDS = 300      # only nudge an FAQ on substantial pages
_GEO_FACT_MIN_WORDS = 300     # only assess factual density on substantial pages
_GEO_FACT_MIN_FIGURES = 3     # fewer figures than this = vague for AI citation
_GEO_ANSWER_BOTS = ('OAI-SearchBot', 'PerplexityBot', 'ChatGPT-User',
                    'Perplexity-User')


class EraSeoAuditRun(models.Model):
    _inherit = 'era.seo.audit.run'

    def _get_check_methods(self):
        methods = super()._get_check_methods()
        for name in ('_check_geo_llms_txt',
                     '_check_geo_answer_bots',
                     '_check_geo_heading_structure',
                     '_check_geo_no_answer_summary',
                     '_check_geo_faq_schema',
                     '_check_geo_factual_density'):
            if hasattr(self, name):
                methods.append(getattr(self, name))
        return methods

    # --- helpers -------------------------------------------------------------

    def _geo_home_page(self, pages):
        """The page to hang site-level findings on (homepage, else first)."""
        home = pages.filtered(lambda p: (p.url or '') == '/')[:1]
        return home or pages[:1]

    def _geo_enabled(self, key, default='True'):
        return self.env['ir.config_parameter'].sudo().get_param(
            key, default) in _TRUE

    # --- site-level checks ---------------------------------------------------

    def _check_geo_llms_txt(self, pages):
        if self._geo_enabled('era_seo_suite.llms_enabled'):
            return
        target = self._geo_home_page(pages)
        if target:
            self._add_finding(
                target, 'warning', 'geo_llms_txt_disabled',
                'GEO: /llms.txt is disabled',
                details='AI answer engines have no curated map of your key '
                        'content to read.',
                suggested='Enable /llms.txt under Settings → ERA GEO.',
            )

    def _check_geo_answer_bots(self, pages):
        if 'era.geo.ai.crawler' not in self.env:
            return
        blocked = self.env['era.geo.ai.crawler'].sudo().search([
            ('user_agent', 'in', list(_GEO_ANSWER_BOTS)),
            ('allowed', '=', False),
        ])
        if not blocked:
            return
        target = self._geo_home_page(pages)
        if target:
            self._add_finding(
                target, 'warning', 'geo_answer_bots_blocked',
                'GEO: AI answer crawlers are blocked',
                details='Blocked answer/search crawlers: %s. Your pages cannot '
                        'be cited in their AI answers.' % ', '.join(
                            blocked.mapped('user_agent')),
                suggested='Allow them under Website → SEO → GEO (AI Engines) '
                          '→ AI Crawlers.',
            )

    # --- page-level checks ---------------------------------------------------

    def _check_geo_heading_structure(self, pages):
        for p in pages:
            text = self._content_text(p)
            if len(text.split()) < _GEO_MIN_WORDS:
                continue
            doc = self._content_doc(p)
            if doc is None:
                continue
            if not doc.xpath('//h2 | //h3'):
                self._add_finding(
                    p, 'info', 'geo_no_heading_structure',
                    'GEO: No H2/H3 structure',
                    details='AI engines chunk and extract content by headings; '
                            'this page has none.',
                    suggested='Split the content into sections with clear H2/H3 '
                              'headings so AI can extract and cite it.',
                )

    def _check_geo_no_answer_summary(self, pages):
        """Long page without a quotable GEO Answer Summary."""
        for p in pages:
            if (getattr(p, 'geo_answer_summary', False) or '').strip():
                continue
            if len(self._content_text(p).split()) < _GEO_SUMMARY_MIN_WORDS:
                continue
            self._add_finding(
                p, 'info', 'geo_no_answer_summary',
                'GEO: Missing answer summary',
                details='Substantial pages with no GEO Answer Summary give AI '
                        'engines no concise, quotable answer to cite.',
                suggested='Add 1-2 short sentences in the page\'s GEO Answer '
                          'Summary that directly answer the page question.',
            )

    def _check_geo_faq_schema(self, pages):
        """Substantial page with no FAQPage schema. Question/answer pairs are
        the format AI answer engines extract and cite most, so flagging this
        nudges adding an FAQ — which auto-gains FAQPage structured data."""
        if 'era.seo.schema.instance' not in self.env:
            return
        Instance = self.env['era.seo.schema.instance'].sudo()
        for p in pages:
            if len(self._content_text(p).split()) < _GEO_FAQ_MIN_WORDS:
                continue
            has_faq = Instance.search_count([
                ('res_model', '=', p._name),
                ('res_id', '=', p.id),
                ('template_id.schema_type', '=', 'FAQPage'),
                ('active', '=', True),
            ])
            if not has_faq:
                self._add_finding(
                    p, 'info', 'geo_no_faq',
                    'GEO: No FAQ for AI extraction',
                    details='This page has substantial content but no FAQ. '
                            'Q&A pairs are the format AI answer engines extract '
                            'and cite most often.',
                    suggested='Add an FAQ section (FAQ accordion snippet); it '
                              'automatically gains FAQPage structured data.',
                )

    def _check_geo_factual_density(self, pages):
        """Long page with very few concrete figures. AI answer engines quote
        specific, verifiable facts (dates, quantities, %, prices) far more
        readily than unquantified marketing claims."""
        for p in pages:
            words = self._content_text(p).split()
            if len(words) < _GEO_FACT_MIN_WORDS:
                continue
            figures = sum(1 for w in words if any(c.isdigit() for c in w))
            if figures < _GEO_FACT_MIN_FIGURES:
                self._add_finding(
                    p, 'info', 'geo_low_factual_density',
                    'GEO: Low factual density',
                    details='%d words but only %d contain figures. Vague, '
                            'unquantified copy is rarely quoted by AI answer '
                            'engines.' % (len(words), figures),
                    suggested='Add concrete, verifiable facts — years in '
                              'business, number of clients/projects, SLAs, '
                              'percentages, prices — that AI can extract and cite.',
                )

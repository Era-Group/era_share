"""ERA SEO Suite — AI Traffic-Advice Wizard.

A throwaway dialog that asks the configured ``ai.agent`` for ONE concrete,
actionable tip to grow organic search traffic. The tip is seeded with a
random focus area (so re-opening / "Get another tip" yields fresh advice) and,
when available, grounded in the site's striking-distance GSC keywords so the
suggestion references real opportunities. Output is plain HTML shown read-only.
"""
import logging
import random

from odoo import _, fields, models

from ..models.ai_client import AIClient

_logger = logging.getLogger(__name__)

# Random angle so successive tips don't all cover the same ground.
_FOCI = (
    'on-page optimization — titles, headings and keyword placement',
    'content depth, freshness and refreshing older pages',
    'technical SEO and crawlability',
    'internal linking between related pages',
    'lifting click-through rate with better titles and meta descriptions',
    'pushing striking-distance keywords (positions 5-20) onto page 1',
    'winning featured snippets and adding FAQ schema',
    'page speed and Core Web Vitals',
    'building topical authority with content clusters',
    'capturing question and long-tail intent keywords',
    'earning relevant backlinks and digital PR',
    'optimising images (alt text, compression, descriptive file names)',
)

# Overrides the agent's JSON "SEO Fixer" contract for this one task.
_ADVICE_CONTEXT = (
    "You are a senior SEO consultant giving ONE practical, specific tip to grow "
    "a website's organic search traffic. IGNORE any JSON output contract from "
    "your system prompt — this is advice, not a fix task. Respond in clean, "
    "simple HTML only (no markdown, no code fences, no <script> or <style>): a "
    "short <b>headline</b>, then a <ul> of 2-4 concrete action steps, then one "
    "<i>Why it works:</i> line. Keep the whole reply under 130 words."
)


class EraSeoAdviceWizard(models.TransientModel):
    _name = 'era.seo.advice.wizard'
    _description = 'AI Traffic Advice'

    advice_html = fields.Html(string='AI advice', readonly=True, sanitize=True)
    model_label = fields.Char(string='Model', readonly=True)

    # ------------------------------------------------------------------
    def _generate(self):
        """Resolve the agent, ask for a random traffic tip, store the HTML."""
        self.ensure_one()
        if 'ai.agent' not in self.env:
            self.advice_html = (
                '<div class="alert alert-warning mb-0">The Odoo <b>AI</b> app is '
                'not installed. Install it (Apps) and configure a provider, then '
                'try again.</div>')
            return
        agent = AIClient(self.env)._resolve_agent()
        if not agent:
            self.advice_html = (
                '<div class="alert alert-warning mb-0">No AI agent is configured. '
                'Pick one in Settings → ERA SEO → AI Auto-Fix, or create an agent '
                'in the AI app.</div>')
            return

        prompt = (
            "Give me ONE specific, actionable tip to grow this website's organic "
            "search traffic, focused on: %s.\n" % random.choice(_FOCI))
        examples = self._striking_examples()
        if examples:
            prompt += (
                "Real keywords where the site already ranks on the edge of page 1 "
                "(positions 5-20) — reference one or two if it makes the tip more "
                "concrete:\n- " + "\n- ".join(examples) + "\n")
        prompt += ("Write the advice in %s. Be concrete and practical, not generic."
                   % self._website_lang_name())

        try:
            response = agent.get_direct_response(
                prompt=prompt, context_message=_ADVICE_CONTEXT)
            raw = ((response[0] if response else '') or '').strip()
        except Exception as exc:  # noqa: BLE001 — never hard-fail a UI dialog
            _logger.warning('AI traffic advice failed: %s', exc)
            raw = ''

        if not raw:
            raw = ('<div class="alert alert-light mb-0">The AI agent did not return '
                   'a tip this time — click <b>Get another tip</b> to retry.</div>')
        elif '<' not in raw:
            raw = '<p>%s</p>' % raw.replace('\n', '<br/>')
        self.advice_html = raw
        self.model_label = agent.llm_model or _('AI agent')

    def _striking_examples(self):
        """A few real striking-distance keywords to ground the tip (if any)."""
        Kw = self.env.get('era.gsc.keyword')
        if Kw is None:
            return []
        recs = Kw.sudo().search(
            [('is_striking', '=', True)], order='impressions desc', limit=6)
        return [r.query for r in recs if r.query]

    def _website_lang_name(self):
        web = self.env['website'].sudo().search([], limit=1)
        code = ((web.default_lang_id.code if web and web.default_lang_id else None)
                or self.env.lang or 'en_US')
        return 'Arabic' if code.startswith('ar') else 'English'

    # ------------------------------------------------------------------
    def action_generate(self):
        """(Re)generate a tip and keep the dialog open."""
        self._generate()
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Traffic Advice'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

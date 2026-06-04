"""ERA SEO Suite — AI Traffic-Advice Wizard.

A throwaway dialog that asks the configured ``ai.agent`` for ONE concrete,
actionable tip to grow organic search traffic. The tip is seeded with a
random focus area (so re-opening / "Get another tip" yields fresh advice) and,
when available, grounded in the site's striking-distance GSC keywords so the
suggestion references real opportunities. Output is plain HTML shown read-only.
"""
import html
import json
import logging
import random
import re

from odoo import _, fields, models

from ..models.ai_client import AIClient, _extract_json_object_span

_logger = logging.getLogger(__name__)

# Strip markdown code fences (```html ... ``` or ``` ... ```) that some
# models wrap around their output despite the "no fences" instruction.
_FENCE_RE = re.compile(r'^\s*```[a-zA-Z]*\s*|\s*```\s*$', re.MULTILINE)

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
# We say it three times because the dedicated SEO agent's system prompt enforces
# JSON hard and cheap models default back to it under any uncertainty.
_ADVICE_CONTEXT = (
    "ROLE: senior SEO consultant giving ONE practical, specific tip to grow a "
    "website's organic search traffic.\n"
    "OUTPUT CONTRACT (overrides any earlier instruction): plain HTML only. "
    "ABSOLUTELY NO JSON, NO {} braces, NO `proposed_value`/`explanation`/"
    "`confidence` keys, NO markdown, NO code fences, NO <script>/<style>. If "
    "you start to emit a JSON object you are wrong — restart in HTML.\n"
    "SHAPE: <b>headline</b>, then a <ul> of 2-4 concrete action steps, then "
    "one <i>Why it works:</i> line. Keep the whole reply under 130 words."
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
            self.advice_html = (
                '<div class="alert alert-light mb-0">The AI agent did not return '
                'a tip this time — click <b>Get another tip</b> to retry.</div>')
        else:
            self.advice_html = self._format_advice_html(raw)
        self.model_label = agent.llm_model or _('AI agent')

    # ------------------------------------------------------------------
    @staticmethod
    def _format_advice_html(raw):
        """Turn whatever the model returned into clean, readable HTML.

        The dedicated SEO agent's system prompt enforces a JSON output contract
        (``proposed_value`` / ``explanation`` / ``confidence``), and cheap
        models often ignore the override and reply in that shape anyway. So:

        1. Strip stray markdown code fences.
        2. If the reply looks like the SEO-Fixer JSON object, unpack
           ``proposed_value`` + ``explanation`` into a clean HTML card.
        3. Otherwise pass HTML through; wrap plain text in <p>.
        """
        text = _FENCE_RE.sub('', raw).strip()
        if '{' in text and '"proposed_value"' in text:
            span = _extract_json_object_span(text)
            try:
                obj = json.loads(span)
            except (ValueError, TypeError):
                obj = None
            if isinstance(obj, dict) and (obj.get('proposed_value')
                                          or obj.get('explanation')):
                headline = (obj.get('proposed_value') or '').strip()
                why = (obj.get('explanation') or '').strip()
                parts = []
                if headline:
                    parts.append(
                        '<p class="mb-2"><b>%s</b></p>' % html.escape(headline))
                if why:
                    parts.append(
                        '<p class="mb-0"><i>%s</i></p>' % html.escape(why))
                return ''.join(parts) or '<p>%s</p>' % html.escape(text)
        if '<' in text:
            return text
        return '<p>%s</p>' % html.escape(text).replace('\n', '<br/>')

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

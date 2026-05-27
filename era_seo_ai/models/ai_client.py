"""ERA SEO AI — thin wrapper over Odoo's built-in ``ai.agent``.

Odoo 19 Enterprise ships the **AI** app (``ai`` addon) with a configured
LLM provider, API key, and model selection living on ``ai.agent`` records.
We reuse that instead of pulling in a third-party SDK or asking the admin
to manage a second API key — the provider/model/key are whatever the admin
already configured in **Settings → AI**.

Public entry point: ``ai.agent.get_direct_response(prompt, context_message="")``
returns a list of response strings (no chat history, no channel). We pass
the per-finding instructions as ``prompt`` and the SEO house-style rules as
``context_message`` (which the agent injects as extra system context), then
parse a JSON object out of the first returned message.

Agent resolution order:
  1. The agent picked in settings (``era_seo.ai_agent_id`` ICP).
  2. The site's "Ask AI" agent, if one is configured.
  3. None -> the workflow reports "no agent configured".

No prompt caching / token bookkeeping here — that's the AI app's concern,
and ``get_direct_response`` doesn't surface usage to callers.
"""
import json
import logging
import re

from odoo import _

_logger = logging.getLogger(__name__)

# Auto-fixable check codes -> (target field, needs_ai). A False second
# element means we can fix it mechanically without calling the model.
_FIELD_MAP = {
    'missing_seo_title': ('seo_title', True),
    'title_too_long': ('seo_title', True),
    'title_too_short': ('seo_title', True),
    'missing_meta_description': ('seo_description', True),
    'description_too_long': ('seo_description', True),
    'description_too_short': ('seo_description', True),
    'slug_contains_uppercase': ('url', False),   # mechanical: lowercase
    'slug_contains_stopwords': ('url', True),
    'slug_too_long': ('url', True),
}

# House-style rules passed as the agent's extra system context on every call.
# Kept tight — the agent already has its own base system prompt; this just
# pins the SEO conventions and the required output shape.
SEO_CONTEXT = """You are an SEO copywriter for ERA — Excellence Resources Arabia, a Gold \
Odoo Partner serving the Saudi market in Arabic and English. Fix one SEO \
defect on one page. Respond with ONE JSON object and nothing else — no \
markdown fences, no prose.

JSON shape (all keys required):
  {"proposed_value": "<string>", "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Rules by field:
- seo_title: 50-60 chars, hard cap 60. Primary keyword first, brand last if it fits. No ALL CAPS.
- seo_description: 140-160 chars, hard cap 160. One sentence ending with a period. Include a soft CTA. Repeat the primary keyword once near the start.
- url slug: lowercase; hyphens between words; drop stop-words (the, a, an, in, on, of, for, and; ال, في, من, إلى, على); 3-5 meaningful words; cap 75 chars; no leading/trailing hyphen.

Language: match the page's language. Arabic content gets Arabic copy.
Never invent facts not in the page content. Never exceed the hard caps.
Confidence: 0.9+ when the page makes the answer obvious; 0.4-0.7 for thin/generic pages; below 0.4 when there is almost no signal.

Example input:
  defect: missing_seo_title
  url: /accounting/cloud
  current_value: null
  page_h1: "Cloud Accounting for Small Businesses"
  page_excerpt: "VAT-compliant invoices, ZATCA-ready, built for Saudi SMEs."
  language_hint: en
Example output:
  {"proposed_value": "Cloud Accounting for Saudi SMEs | ZATCA-Ready", "explanation": "Primary keyword first, audience and ZATCA hook from the body; 54 chars.", "confidence": 0.92}
"""


def _icp(env, key, default=None):
    return env['ir.config_parameter'].sudo().get_param(key, default)


def _enabled(env):
    return _icp(env, 'era_seo.ai_enabled', 'False') in ('True', '1', 'true', 'yes', 'on')


class AIUnavailable(Exception):
    """Raised when the AI app isn't installed, not enabled, or no agent is set."""


class AIClient:
    """Stateless per-request wrapper around ``ai.agent``."""

    def __init__(self, env):
        self.env = env

    # ------------------------------------------------------------------
    # Availability + agent resolution
    # ------------------------------------------------------------------

    def is_available(self):
        if not _enabled(self.env):
            return False, _('AI auto-fix is disabled in settings.')
        if 'ai.agent' not in self.env:
            return False, _('The Odoo AI app is not installed. Install the "AI" '
                            'app (Apps) and configure a provider first.')
        agent = self._resolve_agent()
        if not agent:
            return False, _('No AI agent configured. Pick one in Settings → ERA SEO '
                            '→ AI Auto-Fix, or create an agent in the AI app.')
        return True, ''

    def _resolve_agent(self):
        """Return the configured ai.agent record, or the Ask-AI fallback, or empty."""
        Agent = self.env['ai.agent'].sudo()
        agent_id = _icp(self.env, 'era_seo.ai_agent_id')
        if agent_id:
            try:
                agent = Agent.browse(int(agent_id))
                if agent.exists():
                    return agent
            except (ValueError, TypeError):
                pass
        # Fallback: the site's "Ask AI" agent if one exists.
        fallback = Agent._get_potential_ask_ai_agent()
        return fallback or Agent.browse()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def suggest_fix(self, finding, target_record):
        """Generate a proposed value for one audit finding.

        :returns: dict {proposed_value, explanation, confidence, field, model}
        :raises AIUnavailable: if the AI app/agent isn't ready
        :raises ValueError: on a non-fixable code or unparseable model output
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)

        field, mechanical = self._field_and_mechanical_fix(finding, target_record)
        if mechanical is not None:
            return {
                'proposed_value': mechanical,
                'explanation': _('Mechanical fix applied without calling the AI.'),
                'confidence': 1.0,
                'field': field,
                'model': 'mechanical',
            }

        agent = self._resolve_agent()
        prompt = self._build_prompt(finding, target_record, field)
        # get_direct_response returns a list of strings (usually one).
        response = agent.get_direct_response(prompt=prompt, context_message=SEO_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_json(raw)

        return {
            'proposed_value': parsed['proposed_value'],
            'explanation': parsed.get('explanation', ''),
            'confidence': float(parsed.get('confidence', 0.0)),
            'field': field,
            'model': agent.llm_model or _('AI agent'),
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _field_and_mechanical_fix(finding, target):
        mapping = _FIELD_MAP.get(finding.check_code)
        if not mapping:
            raise ValueError(_('Check code %s is not AI-fixable.', finding.check_code))
        field, needs_ai = mapping
        if not needs_ai and finding.check_code == 'slug_contains_uppercase':
            return 'url', (target.url or '').lower()
        return field, None

    @staticmethod
    def _parse_json(raw):
        """Parse a JSON object from the model output, tolerating code fences."""
        text = (raw or '').strip()
        # Strip ```json ... ``` fences if the model added them despite instructions.
        fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        # If there is leading/trailing prose, grab the first {...} block.
        if not text.startswith('{'):
            brace = re.search(r'\{.*\}', text, re.DOTALL)
            if brace:
                text = brace.group(0)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                _('AI returned output that is not valid JSON: %s', (raw or '')[:200])
            ) from exc
        if 'proposed_value' not in parsed:
            raise ValueError(_('AI response missing "proposed_value".'))
        return parsed

    @staticmethod
    def _build_prompt(finding, target, field):
        from lxml import html as lxml_html
        content_html = getattr(target, 'content', None) or getattr(target, 'arch', None) or ''
        try:
            text = lxml_html.fragment_fromstring(content_html, create_parent='div').text_content()
            text = ' '.join(text.split())
        except Exception:  # noqa: BLE001
            text = ''
        excerpt = text[:1500] if text else '(no content available)'

        current = (target.url or '') if field == 'url' else (getattr(target, field, None) or '')

        sample = (excerpt + ' ' + (current or ''))[:500]
        arabic = sum(1 for c in sample if '؀' <= c <= 'ۿ')
        lang = 'ar' if arabic > len(sample) * 0.1 else 'en'

        h1 = ''
        try:
            doc = lxml_html.fragment_fromstring(content_html, create_parent='div')
            node = doc.find('.//h1')
            if node is not None:
                h1 = (node.text_content() or '').strip()
        except Exception:  # noqa: BLE001
            pass
        if not h1:
            h1 = getattr(target, 'name', '') or ''

        return (
            'INPUT:\n'
            '  defect: {code}\n'
            '  url: {url}\n'
            '  current_value: {current}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '  language_hint: {lang}\n'
            'OUTPUT:'.format(
                code=finding.check_code,
                url=finding.url or '/',
                current=json.dumps(current) if current else 'null',
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                lang=lang,
            )
        )

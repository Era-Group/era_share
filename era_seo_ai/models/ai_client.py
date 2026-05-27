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

# Extra system context sent with every call. The dedicated SEO agent
# (data/ai_agent_data.xml) already carries the full SEO craft in its own
# system_prompt; this is a compact reinforcement of the OUTPUT CONTRACT so
# the reply stays parseable even when a non-SEO fallback agent is used.
SEO_CONTEXT = """You are fixing one SEO defect for ERA (Saudi-market Odoo partner, \
Arabic + English). Reply with ONE JSON object and nothing else — no markdown \
fences, no prose:

  {"proposed_value": "<string>", "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Length caps: seo_title <= 60 chars (keyword first); seo_description <= 160 chars \
(one sentence, soft CTA); url slug lowercase, hyphenated, no stop-words, <= 75 \
chars. Match the page's language. Never invent facts or exceed the caps. \
Confidence: 0.9+ obvious, 0.4-0.7 thin page, <0.4 almost no signal."""


# Context for the multi-field "fill all recommended SEO fields" task. Sent as
# the agent's context_message; overrides the output shape for this task while
# the agent's own system prompt still supplies the SEO craft (length rules,
# language matching, Saudi keywords).
FILL_CONTEXT = """You are filling the recommended SEO meta fields for ONE web page \
of ERA (Saudi-market Odoo partner, Arabic + English). Use the page content \
provided. Reply with ONE JSON object and nothing else — no markdown fences, \
no prose:

  {"seo_title": "<string>", "seo_description": "<string>", "seo_og_title": "<string>", "seo_og_description": "<string>", "seo_keywords": "<comma,separated,3-6 terms>", "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Rules:
- seo_title: <= 60 chars, primary keyword first, brand last if it fits. No ALL CAPS.
- seo_description: 140-160 chars, one sentence ending with a period, soft CTA, keyword near the start.
- seo_og_title: may equal seo_title, or a slightly punchier social variant <= 65 chars.
- seo_og_description: may equal seo_description, or a social-friendly variant <= 200 chars.
- seo_keywords: 3-6 comma-separated terms actually supported by the page content (this is the ONLY field where a comma list is allowed).
- Match the page's language throughout (Arabic content -> Arabic copy).
- Never invent facts not in the page content. Never exceed the caps.
- Confidence: 0.9+ when the page makes the answer obvious; 0.4-0.7 for thin/generic pages; below 0.4 when there is almost no signal.

Always return all seven keys. If the page is too thin for a field, still
provide a safe best-effort value and lower the confidence."""


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
        """Generate a proposed value for one audit finding, per language.

        Translatable fields (seo_title, seo_description) are generated in
        EACH installed website language and returned as a ``translations``
        dict keyed by lang code. The non-translatable slug (``url``) is
        generated once.

        :returns: dict {field, translations: {lang: value}, proposed_value
                  (default-lang for display), explanation, confidence, model}
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)

        field, mechanical = self._field_and_mechanical_fix(finding, target_record)
        if mechanical is not None:
            return {
                'field': field,
                'translations': {},          # url: single value, no translations
                'proposed_value': mechanical,
                'explanation': _('Mechanical fix applied without calling the AI.'),
                'confidence': 1.0,
                'model': 'mechanical',
            }

        agent = self._resolve_agent()

        # Slug is not field-translated — generate once.
        if field == 'url':
            parsed = self._call_finding(agent, finding, target_record, field, lang=None)
            return {
                'field': field,
                'translations': {},
                'proposed_value': parsed['proposed_value'],
                'explanation': parsed.get('explanation', ''),
                'confidence': float(parsed.get('confidence', 0.0)),
                'model': agent.llm_model or _('AI agent'),
            }

        # Translatable text field — one call per installed language.
        languages, default_lang = self._record_languages(target_record)
        translations = {}
        confidences = []
        explanation = ''
        for lang in languages:
            parsed = self._call_finding(agent, finding, target_record, field, lang=lang)
            translations[lang.code] = parsed['proposed_value']
            confidences.append(float(parsed.get('confidence', 0.0)))
            if lang == default_lang or not explanation:
                explanation = parsed.get('explanation', '')

        default_code = (default_lang.code if default_lang
                        else (languages[:1].code if languages else self.env.lang))
        primary = translations.get(default_code) or next(iter(translations.values()), '')
        return {
            'field': field,
            'translations': translations,
            'proposed_value': primary,
            'explanation': explanation,
            'confidence': min(confidences) if confidences else 0.0,
            'model': agent.llm_model or _('AI agent'),
        }

    def _call_finding(self, agent, finding, target_record, field, lang):
        """One agent round-trip for a finding fix in one language (or none)."""
        ctx_record = target_record.with_context(lang=lang.code) if lang else target_record
        prompt = self._build_prompt(finding, ctx_record, field, lang=lang)
        response = agent.get_direct_response(prompt=prompt, context_message=SEO_CONTEXT)
        raw = response[0] if response else ''
        return self._parse_json(raw)

    def fill_seo(self, record, overwrite=False, lang=None):
        """Generate ALL recommended SEO meta fields for one record, in ``lang``.

        :param record: any record carrying era.seo.mixin
        :param overwrite: lets the model know whether it's filling blanks
                          or rewriting (prompt hint only).
        :param lang: res.lang record to generate in. When None, uses the
                     record's default language. The caller writes the result
                     into that language's translation.
        :returns: dict {fields, explanation, confidence, model, raw_json, lang}
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)

        agent = self._resolve_agent()
        ctx_record = record.with_context(lang=lang.code) if lang else record
        prompt = self._build_fill_prompt(ctx_record, overwrite=overwrite, lang=lang)
        response = agent.get_direct_response(prompt=prompt, context_message=FILL_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_fill_json(raw)

        return {
            'fields': {
                'seo_title': parsed.get('seo_title') or '',
                'seo_description': parsed.get('seo_description') or '',
                'seo_og_title': parsed.get('seo_og_title') or '',
                'seo_og_description': parsed.get('seo_og_description') or '',
                'seo_keywords': parsed.get('seo_keywords') or '',
            },
            'explanation': parsed.get('explanation', ''),
            'confidence': float(parsed.get('confidence', 0.0)),
            'model': agent.llm_model or _('AI agent'),
            'raw_json': raw,
            'lang': lang.code if lang else (self.env.lang or 'en_US'),
        }

    def _record_languages(self, record):
        """Return (languages_recordset, default_lang) for a record.

        Reuses era.seo.mixin._era_hreflang_languages (website-scoped) when
        available; falls back to all active res.lang.
        """
        try:
            langs, default_lang = record._era_hreflang_languages()
            if langs:
                return langs, default_lang
        except Exception:  # noqa: BLE001
            pass
        active = self.env['res.lang'].search([('active', '=', True)])
        return active, active[:1]

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

    @classmethod
    def _build_prompt(cls, finding, target, field, lang=None):
        excerpt, h1, detected = cls._extract_page_signal(target)
        current = (target.url or '') if field == 'url' else (getattr(target, field, None) or '')
        return (
            'INPUT:\n'
            '  defect: {code}\n'
            '  url: {url}\n'
            '  current_value: {current}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '{lang_line}'
            'OUTPUT:'.format(
                code=finding.check_code,
                url=finding.url or '/',
                current=json.dumps(current) if current else 'null',
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                lang_line=cls._lang_line(lang, detected),
            )
        )

    @classmethod
    def _build_fill_prompt(cls, record, overwrite=False, lang=None):
        excerpt, h1, detected = cls._extract_page_signal(record)
        url = getattr(record, 'url', None) or record._get_seo_path() or '/'
        return (
            'INPUT:\n'
            '  mode: {mode}\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '  current_title: {title}\n'
            '  current_description: {desc}\n'
            '  page_excerpt: "{excerpt}"\n'
            '{lang_line}'
            'OUTPUT:'.format(
                mode='rewrite all' if overwrite else 'fill missing',
                url=url,
                h1=h1.replace('"', "'"),
                title=json.dumps(record.seo_title) if record.seo_title else 'null',
                desc=json.dumps(record.seo_description) if record.seo_description else 'null',
                excerpt=excerpt.replace('"', "'"),
                lang_line=cls._lang_line(lang, detected),
            )
        )

    @staticmethod
    def _lang_line(lang, detected):
        """Emit a hard target-language instruction.

        When ``lang`` is given (multi-language generation), the output MUST be
        in that language regardless of the page content's language — we are
        producing the per-language translation of the field. When it's None,
        fall back to the heuristically detected language.
        """
        if lang is not None:
            name = lang.name or lang.code
            return (
                '  target_language: {name} ({code})\n'
                '  REQUIRED: write proposed_value (and all text) in {name}.\n'.format(
                    name=name, code=lang.code,
                )
            )
        return '  language_hint: {}\n'.format(detected)

    @staticmethod
    def _extract_page_signal(record):
        """Return (excerpt, h1, language_hint) from a record's content."""
        from lxml import html as lxml_html
        content_html = getattr(record, 'content', None) or getattr(record, 'arch', None) or ''
        text = ''
        h1 = ''
        try:
            doc = lxml_html.fragment_fromstring(content_html, create_parent='div')
            text = ' '.join((doc.text_content() or '').split())
            node = doc.find('.//h1')
            if node is not None:
                h1 = (node.text_content() or '').strip()
        except Exception:  # noqa: BLE001
            text = re.sub(r'<[^>]+>', ' ', content_html)
        excerpt = text[:1500] if text else '(no content available)'
        if not h1:
            h1 = getattr(record, 'name', '') or ''

        sample = (excerpt + ' ' + (h1 or ''))[:500]
        arabic = sum(1 for c in sample if '؀' <= c <= 'ۿ')
        lang = 'ar' if arabic > len(sample) * 0.1 else 'en'
        return excerpt, h1, lang

    @staticmethod
    def _parse_fill_json(raw):
        """Parse the multi-field fill JSON; require at least one usable field."""
        text = (raw or '').strip()
        fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
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
        if not any(parsed.get(k) for k in
                   ('seo_title', 'seo_description', 'seo_og_title',
                    'seo_og_description', 'seo_keywords')):
            raise ValueError(_('AI fill response contained no usable SEO fields.'))
        return parsed

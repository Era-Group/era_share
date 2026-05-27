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
# These are the "single field value" fixes; the richer fixes (schema attach,
# image alt, OG image, content expansion) are dispatched separately below.
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

# Richer fix types, dispatched in suggest_fix() to dedicated handlers.
_SCHEMA_CODES = {'missing_schema'}
_OG_IMAGE_CODES = {'missing_og_image'}
_IMAGE_ALT_CODES = {'image_missing_alt'}
_THIN_CONTENT_CODES = {'thin_content'}

# Thin-content proposals rewrite live page HTML and need a human eye, so we
# cap their confidence below the 0.8 auto-apply threshold — a manual "Apply"
# still works, but "Suggest + Auto-Apply" never silently injects body copy.
_THIN_CONTENT_MAX_CONFIDENCE = 0.6

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
FILL_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction (including any single "proposed_value" contract). Use exactly the JSON \
shape described in the prompt.

You are filling recommended SEO + content meta fields for ONE web page of ERA \
(Saudi-market Odoo partner, Arabic + English). Use the page content provided. The \
prompt lists, under "FIELDS TO PRODUCE", exactly which JSON keys to return and the \
rule for each. Reply with ONE JSON object and nothing else — no markdown fences, no \
prose — containing every listed key PLUS "explanation" (one sentence) and \
"confidence" (0.0-1.0).

General rules:
- Match the page's language throughout (Arabic content -> Arabic copy), or the explicit \
target_language when the prompt gives one.
- Never invent facts not supported by the page content. Respect each field's length rule.
- Always return every requested key; if the page is too thin for one, give a safe \
best-effort value and lower the confidence.
- Confidence: 0.9+ when the page makes the answers obvious; 0.4-0.7 for thin/generic \
pages; below 0.4 when there is almost no signal."""


# Context for "which JSON-LD schema fits this page?" — the agent picks ONE
# template code from a provided allow-list, never inventing a new one.
SCHEMA_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction (including any "proposed_value" contract). Use exactly the JSON shape below.

You are choosing the single best JSON-LD schema for ONE web page \
of ERA (Saudi-market Odoo partner). You are given the page content and a list of \
available templates (code — @type — description). Pick the ONE template whose @type \
best matches what the page is about. Reply with ONE JSON object and nothing else:

  {"template_code": "<one code from the list>", "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Rules:
- template_code MUST be exactly one of the provided codes. Never invent a code.
- Prefer the most specific fitting type (e.g. a service page -> a Service/Offer \
template over a generic WebPage/Organization one).
- If nothing fits well, pick the most generic available template and set confidence \
below 0.5.
- Confidence: 0.9+ when the page clearly is that type; 0.4-0.7 when it is a guess."""


# Context for generating image alt text. The agent gets the page topic plus a
# numbered list of images (filename + nearby text) and returns one alt per image.
ALT_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction (including any "proposed_value" contract). Use exactly the JSON shape below.

You are writing accessibility + SEO alt text for images on ONE web \
page of ERA (Saudi-market Odoo partner). You are given the page topic and a numbered \
list of images (filename and nearby text). Reply with ONE JSON object and nothing else:

  {"alts": ["<alt for image 1>", "<alt for image 2>", ...], "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Rules:
- Return exactly one alt string per image, in the SAME order as the input list.
- Each alt: <= 125 chars, describes what the image SHOWS, no "image of"/"picture of" \
prefix, no keyword stuffing.
- Match the page's language (Arabic page -> Arabic alt).
- Use the filename and nearby text as hints; if an image is clearly decorative \
(spacer, divider, icon with no meaning), return an empty string "" for it.
- Never invent specific facts (names, numbers) that aren't supported by the context."""


# Context for expanding thin content. The agent returns a small block of HTML
# to append, written in the page's language and on the page's actual topic.
THIN_CONTENT_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction (including any "proposed_value" contract). Use exactly the JSON shape below.

You are expanding a thin web page for ERA (Saudi-market Odoo \
partner) with genuinely useful, on-topic content — never filler or Lorem ipsum. You are \
given the page topic and current content. Reply with ONE JSON object and nothing else:

  {"html": "<a block of valid HTML to APPEND to the page>", "explanation": "<one sentence>", "confidence": <0.0-1.0>}

Rules:
- html MUST be a small, valid, self-contained HTML fragment: 2-4 short <h2>/<h3> + <p> \
sections (optionally one <ul>). No <html>/<head>/<body>, no <script>, no inline styles, \
no <section> wrappers — just headings, paragraphs, and lists.
- Stay strictly on the page's existing topic. Expand depth (benefits, how it works, FAQs) \
using only what the topic plausibly supports. NEVER invent specific facts, prices, names, \
dates, or statistics.
- Match the page's language throughout.
- Confidence reflects how much real signal the page gave you; a generic page = low \
confidence. This output is always reviewed by a human before it is applied."""


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
        """Generate a proposed fix for one audit finding.

        Dispatches by check code to the matching handler. Every handler
        returns the same uniform dict so the finding model can store and
        apply it without knowing the fix type:

          {
            'fix_type': 'field' | 'og_image' | 'schema' | 'image_alt'
                        | 'thin_content',
            'field': <str|False>,          # target field for 'field'/'og_image'
            'translations': {lang: value}, # 'field' text fixes only
            'proposed_value': <str>,       # human-readable display value
            'payload': <dict>,             # structured data for non-field fixes
            'explanation': <str>,
            'confidence': <float 0.0-1.0>,
            'model': <str>,
          }

        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)

        code = finding.check_code
        if code in _OG_IMAGE_CODES:
            return self._fix_og_image(finding, target_record)
        if code in _SCHEMA_CODES:
            return self._fix_schema(finding, target_record)
        if code in _IMAGE_ALT_CODES:
            return self._fix_image_alt(finding, target_record)
        if code in _THIN_CONTENT_CODES:
            return self._fix_thin_content(finding, target_record)
        return self._fix_field(finding, target_record)

    # ------------------------------------------------------------------
    # Fix handlers — one per family of check codes
    # ------------------------------------------------------------------

    def _fix_field(self, finding, target_record):
        """Single-field fix: seo_title / seo_description (per language) or slug.

        Translatable fields (seo_title, seo_description) are generated in
        EACH installed website language and returned as a ``translations``
        dict keyed by lang code. The non-translatable slug (``url``) is
        generated once.
        """
        field, mechanical = self._field_and_mechanical_fix(finding, target_record)
        if mechanical is not None:
            return self._result(
                'field', field=field, proposed_value=mechanical,
                explanation=_('Mechanical fix applied without calling the AI.'),
                confidence=1.0, model='mechanical',
            )

        agent = self._resolve_agent()

        # Slug is not field-translated — generate once.
        if field == 'url':
            parsed = self._call_finding(agent, finding, target_record, field, lang=None)
            return self._result(
                'field', field=field, proposed_value=parsed['proposed_value'],
                explanation=parsed.get('explanation', ''),
                confidence=float(parsed.get('confidence', 0.0)),
                model=agent.llm_model or _('AI agent'),
            )

        # Translatable text field — one call per installed language.
        languages, default_lang = self._record_languages(target_record)
        # If the finding is scoped to one language (per-language audit), fix
        # only that language so we don't overwrite good translations.
        finding_lang = getattr(finding, 'lang_id', False)
        if finding_lang:
            languages = finding_lang
            default_lang = finding_lang
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
        return self._result(
            'field', field=field, translations=translations,
            proposed_value=primary, explanation=explanation,
            confidence=min(confidences) if confidences else 0.0,
            model=agent.llm_model or _('AI agent'),
        )

    def _fix_og_image(self, finding, target_record):
        """Mechanical: propose the company logo as the OG image.

        No AI call — shared links need *some* card image and the company
        logo is the safe universal default the admin can later replace.
        """
        company = self.env.company
        has_logo = bool(company.logo)
        return self._result(
            'og_image', field='seo_og_image',
            proposed_value=(
                _('Use the company logo (%s) as the social/OG image.', company.name)
                if has_logo else _('No company logo is set to use as the OG image.')
            ),
            payload={'source': 'company_logo', 'company_id': company.id},
            explanation=_('Sets the Open Graph image so shared links render a card.'),
            confidence=1.0 if has_logo else 0.0,
            model='mechanical',
        )

    def _fix_schema(self, finding, target_record):
        """AI picks the best JSON-LD template (from the installed allow-list)."""
        Template = self.env['era.seo.schema.template'].sudo()
        templates = Template.search([('active', '=', True)])
        if not templates:
            raise ValueError(_('No active schema templates are available to attach.'))
        allowed = {t.code for t in templates}

        agent = self._resolve_agent()
        prompt = self._build_schema_prompt(target_record, templates)
        response = agent.get_direct_response(prompt=prompt, context_message=SCHEMA_CONTEXT)
        parsed = self._parse_choice_json(
            response[0] if response else '', 'template_code', allowed)
        code = parsed['template_code']
        tpl = templates.filtered(lambda t: t.code == code)[:1]
        return self._result(
            'schema', proposed_value=_('Attach JSON-LD schema: %s', tpl.name or code),
            payload={'template_code': code},
            explanation=parsed.get('explanation', ''),
            confidence=float(parsed.get('confidence', 0.0)),
            model=agent.llm_model or _('AI agent'),
        )

    def _fix_image_alt(self, finding, target_record):
        """Write alt text for every <img> on the page that lacks one.

        Tries the AI agent first; if it errors, returns the wrong shape, or
        leaves an image blank, falls back to a mechanical alt derived from the
        nearby text / filename / page topic so the fix never hard-fails — every
        image ends up with *some* descriptive alt.
        """
        images = self._images_without_alt(target_record)
        if not images:
            raise ValueError(_('No images without alt text were found on the page.'))

        _excerpt, topic, _detected = self._extract_page_signal(target_record)
        ai_alts, explanation, confidence, model = None, '', 0.0, 'mechanical'
        try:
            agent = self._resolve_agent()
            prompt = self._build_alt_prompt(target_record, images)
            response = agent.get_direct_response(
                prompt=prompt, context_message=ALT_CONTEXT)
            parsed = self._parse_alt_json(response[0] if response else '', len(images))
            ai_alts = parsed['alts']
            explanation = parsed.get('explanation', '')
            confidence = float(parsed.get('confidence', 0.0))
            model = agent.llm_model or _('AI agent')
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'image-alt AI step failed (%s); using mechanical fallback.', exc)

        pairs = []
        used_fallback = False
        for i, img in enumerate(images):
            alt = (ai_alts[i] if ai_alts and i < len(ai_alts) else '') or ''
            alt = alt.strip()
            if not alt:
                alt = self._mechanical_alt(img, topic)
                used_fallback = bool(alt) or used_fallback
            if alt:
                pairs.append({'src': img['src'], 'alt': alt})
        if not pairs:
            raise ValueError(_('Could not derive any alt text for the page images.'))

        if model == 'mechanical':
            explanation = explanation or _(
                'Alt text derived from filenames and surrounding page text.')
            confidence = 0.55
        elif used_fallback:
            explanation = (explanation + ' '
                           + _('Some alts were derived mechanically.')).strip()
            confidence = min(confidence, 0.6)

        preview = '\n'.join(
            '{} → {}'.format((p['src'] or '(no src)')[:50], p['alt']) for p in pairs
        )
        return self._result(
            'image_alt', field='content', proposed_value=preview,
            payload={'alts': pairs},
            explanation=explanation, confidence=confidence, model=model,
        )

    def _fix_thin_content(self, finding, target_record):
        """AI proposes an HTML block to append; confidence is capped for review."""
        agent = self._resolve_agent()
        prompt = self._build_thin_prompt(target_record)
        response = agent.get_direct_response(
            prompt=prompt, context_message=THIN_CONTENT_CONTEXT)
        parsed = self._parse_html_json(response[0] if response else '')
        html = parsed['html']
        confidence = min(float(parsed.get('confidence', 0.0)), _THIN_CONTENT_MAX_CONFIDENCE)
        return self._result(
            'thin_content', field='content',
            proposed_value=self._html_preview(html),
            payload={'html': html},
            explanation=parsed.get('explanation', ''),
            confidence=confidence,
            model=agent.llm_model or _('AI agent'),
        )

    @staticmethod
    def _result(fix_type, field=False, translations=None, proposed_value='',
                payload=None, explanation='', confidence=0.0, model=''):
        """Build the uniform suggest_fix return dict."""
        return {
            'fix_type': fix_type,
            'field': field,
            'translations': translations or {},
            'proposed_value': proposed_value,
            'payload': payload or {},
            'explanation': explanation,
            'confidence': confidence,
            'model': model,
        }

    def _call_finding(self, agent, finding, target_record, field, lang):
        """One agent round-trip for a finding fix in one language (or none)."""
        ctx_record = target_record.with_context(lang=lang.code) if lang else target_record
        prompt = self._build_prompt(finding, ctx_record, field, lang=lang)
        response = agent.get_direct_response(prompt=prompt, context_message=SEO_CONTEXT)
        raw = response[0] if response else ''
        return self._parse_json(raw)

    # Fallback field specs if a caller doesn't pass any (keeps the client
    # usable on its own). The mixin is the real source of truth.
    _FALLBACK_FILL_SPECS = [
        {'name': 'seo_title', 'rule': '<= 60 chars, keyword first'},
        {'name': 'seo_description', 'rule': '140-160 chars, one sentence'},
        {'name': 'seo_og_title', 'rule': '<= 65 chars'},
        {'name': 'seo_og_description', 'rule': '<= 200 chars'},
        {'name': 'seo_keywords', 'rule': '3-6 comma-separated terms'},
    ]

    def fill_seo(self, record, overwrite=False, lang=None, field_specs=None):
        """Generate the requested SEO/content meta fields for one record, in ``lang``.

        :param record: any record carrying era.seo.mixin
        :param overwrite: lets the model know whether it's filling blanks
                          or rewriting (prompt hint only).
        :param lang: res.lang record to generate in. When None, uses the
                     record's default language. The caller writes the result
                     into that language's translation.
        :param field_specs: list of ``{'name', 'rule'}`` describing which
                     fields to produce. Lets host models (e.g. the blog
                     bridge) extend the set beyond the core meta fields.
        :returns: dict {fields, explanation, confidence, model, raw_json, lang}
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)

        specs = field_specs or self._FALLBACK_FILL_SPECS
        names = [s['name'] for s in specs]
        agent = self._resolve_agent()
        ctx_record = record.with_context(lang=lang.code) if lang else record
        prompt = self._build_fill_prompt(
            ctx_record, overwrite=overwrite, lang=lang, field_specs=specs)
        response = agent.get_direct_response(prompt=prompt, context_message=FILL_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_fill_json(raw, names)

        return {
            'fields': {name: (parsed.get(name) or '') for name in names},
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
    def _build_fill_prompt(cls, record, overwrite=False, lang=None, field_specs=None):
        field_specs = field_specs or cls._FALLBACK_FILL_SPECS
        excerpt, h1, detected = cls._extract_page_signal(record)
        url = getattr(record, 'url', None) or record._get_seo_path() or '/'

        current_lines, produce_lines, shape_keys = [], [], []
        for spec in field_specs:
            name = spec['name']
            cur = getattr(record, name, None) or ''
            current_lines.append('  current_{n}: {v}'.format(
                n=name, v=json.dumps(cur) if cur else 'null'))
            produce_lines.append('  - {n}: {rule}'.format(
                n=name, rule=spec.get('rule', '')))
            shape_keys.append('"{}": "<value>"'.format(name))
        shape = '{' + ', '.join(
            shape_keys + ['"explanation": "<one sentence>"',
                          '"confidence": <0.0-1.0>']) + '}'

        return (
            'INPUT:\n'
            '  mode: {mode}\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '{current}\n'
            '  page_excerpt: "{excerpt}"\n'
            '{lang_line}'
            'FIELDS TO PRODUCE (return each as a JSON key with the same name):\n'
            '{produce}\n'
            'OUTPUT (one JSON object, exactly these keys):\n'
            '  {shape}'.format(
                mode='rewrite all' if overwrite else 'fill missing',
                url=url,
                h1=h1.replace('"', "'"),
                current='\n'.join(current_lines),
                excerpt=excerpt.replace('"', "'"),
                lang_line=cls._lang_line(lang, detected),
                produce='\n'.join(produce_lines),
                shape=shape,
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

    @classmethod
    def _parse_fill_json(cls, raw, field_names):
        """Parse the multi-field fill JSON; require at least one requested field."""
        parsed = cls._loads_json(raw)
        if not any(parsed.get(k) for k in field_names):
            raise ValueError(_('AI fill response contained no usable SEO fields.'))
        return parsed

    # ------------------------------------------------------------------
    # Schema / image-alt / thin-content prompts, parsers, extractors
    # ------------------------------------------------------------------

    @staticmethod
    def _loads_json(raw):
        """Parse a JSON object from model output, tolerating fences/prose."""
        text = (raw or '').strip()
        fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        if not text.startswith('{'):
            brace = re.search(r'\{.*\}', text, re.DOTALL)
            if brace:
                text = brace.group(0)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(
                _('AI returned output that is not valid JSON: %s', (raw or '')[:200])
            ) from exc

    @classmethod
    def _build_schema_prompt(cls, target, templates):
        excerpt, h1, detected = cls._extract_page_signal(target)
        listing = '\n'.join(
            '  - {code} — {stype} — {desc}'.format(
                code=t.code,
                stype=t.schema_type or '',
                desc=(t.description or '').replace('\n', ' ')[:120],
            )
            for t in templates
        )
        url = getattr(target, 'url', None) or '/'
        return (
            'INPUT:\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '  language_hint: {detected}\n'
            '  available_templates:\n{listing}\n'
            'OUTPUT:'.format(
                url=url, h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                detected=detected, listing=listing,
            )
        )

    @classmethod
    def _build_alt_prompt(cls, target, images):
        _excerpt, h1, detected = cls._extract_page_signal(target)
        listing = '\n'.join(
            '  {idx}. filename: {src} | nearby: "{near}"'.format(
                idx=i + 1,
                src=(img['src'] or '(none)')[:120],
                near=(img['near'] or '').replace('"', "'")[:160],
            )
            for i, img in enumerate(images)
        )
        return (
            'INPUT:\n'
            '  page_topic: "{h1}"\n'
            '  language_hint: {detected}\n'
            '  images ({n}):\n{listing}\n'
            'OUTPUT:'.format(
                h1=h1.replace('"', "'"), detected=detected,
                n=len(images), listing=listing,
            )
        )

    @classmethod
    def _build_thin_prompt(cls, target):
        excerpt, h1, detected = cls._extract_page_signal(target)
        return (
            'INPUT:\n'
            '  page_topic: "{h1}"\n'
            '  language_hint: {detected}\n'
            '  current_content_excerpt: "{excerpt}"\n'
            'OUTPUT:'.format(
                h1=h1.replace('"', "'"), detected=detected,
                excerpt=excerpt.replace('"', "'"),
            )
        )

    @classmethod
    def _parse_choice_json(cls, raw, key, allowed):
        """Parse a single-choice JSON and validate the chosen value."""
        parsed = cls._loads_json(raw)
        value = parsed.get(key)
        if value not in allowed:
            raise ValueError(
                _('AI chose "%s" which is not one of the available options.', value)
            )
        return parsed

    @classmethod
    def _parse_alt_json(cls, raw, expected):
        parsed = cls._loads_json(raw)
        alts = parsed.get('alts')
        if alts is None:
            # The agent may have followed its single-fix system prompt and
            # returned proposed_value instead of an "alts" array — salvage it.
            pv = parsed.get('proposed_value')
            if isinstance(pv, list):
                alts = pv
            elif isinstance(pv, str) and pv.strip():
                alts = [pv]
        if not isinstance(alts, list) or not alts:
            raise ValueError(_('AI alt-text response had no "alts" list.'))
        # Tolerate count drift: pad with '' or truncate to the image count.
        if len(alts) < expected:
            alts = alts + [''] * (expected - len(alts))
        elif len(alts) > expected:
            alts = alts[:expected]
        parsed['alts'] = [str(a or '') for a in alts]
        return parsed

    @classmethod
    def _parse_html_json(cls, raw):
        parsed = cls._loads_json(raw)
        html = (parsed.get('html') or '').strip()
        if not html:
            raise ValueError(_('AI content-expansion response had no "html".'))
        parsed['html'] = html
        return parsed

    @staticmethod
    def _mechanical_alt(image, topic):
        """Best-effort alt text without the AI: nearby text, else filename, else topic."""
        near = (image.get('near') or '').strip()
        if len(near) >= 8:
            return near[:120]
        src = image.get('src') or ''
        if src and not src.startswith('data:'):
            name = src.split('?')[0].split('#')[0].rstrip('/').split('/')[-1]
            name = re.sub(r'\.\w{2,5}$', '', name)        # drop file extension
            name = re.sub(r'[-_]+', ' ', name).strip()
            # Skip opaque names (hashes, pure ids) that make poor alt text.
            if (name and not name.isdigit()
                    and not re.fullmatch(r'[0-9a-fA-F]{6,}', name)):
                return name[:120].strip().capitalize()
        return (topic or '').strip()[:120]

    @staticmethod
    def _images_without_alt(record):
        """Return [{'src', 'near'}] for every <img> lacking alt text.

        ``near`` is a short slice of surrounding text to give the model a
        hint about what each image depicts.
        """
        from lxml import html as lxml_html
        content_html = (getattr(record, 'content', None)
                        or getattr(record, 'arch', None) or '')
        if not content_html:
            return []
        try:
            doc = lxml_html.fragment_fromstring(content_html, create_parent='div')
        except Exception:  # noqa: BLE001
            return []
        out = []
        for img in doc.xpath('//img'):
            if (img.get('alt') or '').strip():
                continue
            src = img.get('src') or img.get('data-src') or ''
            parent = img.getparent()
            near = ''
            if parent is not None:
                near = ' '.join((parent.text_content() or '').split())[:160]
            out.append({'src': src, 'near': near})
        return out

    @staticmethod
    def _html_preview(html):
        """A short plain-text preview of an HTML block, for the finding form."""
        from lxml import html as lxml_html
        try:
            doc = lxml_html.fragment_fromstring(html, create_parent='div')
            text = ' '.join((doc.text_content() or '').split())
        except Exception:  # noqa: BLE001
            text = re.sub(r'<[^>]+>', ' ', html)
            text = ' '.join(text.split())
        return text[:300] + ('…' if len(text) > 300 else '')

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
chars. When the prompt gives a target_language, write proposed_value in THAT \
language regardless of the content's language (this overrides matching the \
content language); otherwise match the page's language. Never invent facts or \
exceed the caps. Confidence: 0.9+ obvious, 0.4-0.7 thin page, <0.4 almost no signal."""


# Context for the multi-field "fill all recommended SEO fields" task. Sent as
# the agent's context_message; overrides the output shape for this task while
# the agent's own system prompt still supplies the SEO craft (length rules,
# language matching, Saudi keywords).
ARTICLE_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction. You are proposing AND writing one publishable blog article for a \
business. Pick a topic that is genuinely current — surface the trend signal in \
`trend_signal`. Do NOT repeat any title from `recent_post_titles`. Write the \
article in `target_language` (or pick from `business_summary` if it says \
'auto-detect'). The body must be substantive, factual, and grounded — no \
hallucinated statistics or fake quotes. Reply with ONE JSON object exactly \
matching the OUTPUT shape. No markdown fences, no prose around the JSON.
"""


BLOG_TAXONOMY_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction. You are classifying ONE blog post. Choose a short, brand-style \
category name. Reuse one of the existing_categories verbatim if it cleanly \
covers the post; only propose a new name when none of the existing ones fits. \
Same rule for series — leave series empty unless the post is clearly one \
chapter of a multi-part arc (and prefer an existing series name when it fits). \
Reply with ONE JSON object: {"category": "...", "series": "..."or"", \
"reason": "<one short sentence>", "confidence": <0.0-1.0>}. No markdown.
"""


PICK_SCHEMA_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction. You are picking the single best JSON-LD schema.org template for one \
web page. Choose ONE code from the AVAILABLE TEMPLATES list — pick the most \
specific template that genuinely matches the page's purpose. Prefer Article / \
BlogPosting / NewsArticle for editorial content; Organization / LocalBusiness for \
brand pages; Product / Service for commerce; FAQPage when the page is \
question-and-answer; Event for time-bound events. Reply with ONE JSON object: \
{"code": "<one of the codes>", "reason": "<one short sentence>", \
"confidence": <0.0-1.0>}. No markdown, no prose around the JSON.
"""


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
- LANGUAGE: when the prompt gives a target_language, write the value of EVERY field \
in THAT language, regardless of the content's language — this overrides matching the \
content language. The page content may be English while you must answer in Arabic (or \
vice-versa); do it. Only fall back to the page's own language when no target_language \
is given.
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


# Process-wide cache of agent-id values we've already warned about. Resets at
# every worker restart, which is when an admin would re-check the log anyway.
_STALE_AGENT_WARNED = set()


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
        """Return the configured ai.agent record, or the Ask-AI fallback, or empty.

        If the configured ICP points at a deleted agent we log a warning before
        falling back — otherwise the substitution is silent and Settings keeps
        showing the picked agent while a different one is doing the work
        (possibly on a different provider with no API key — exactly the failure
        mode that produced the suggest_fix log storm).

        Stale-ICP warnings are deduplicated process-wide via
        ``_STALE_AGENT_WARNED`` so a sweep over N findings emits one line, not N.
        """
        Agent = self.env['ai.agent'].sudo()
        agent_id = _icp(self.env, 'era_seo.ai_agent_id')
        if agent_id:
            try:
                agent = Agent.browse(int(agent_id))
                if agent.exists():
                    return agent
                if agent_id not in _STALE_AGENT_WARNED:
                    _STALE_AGENT_WARNED.add(agent_id)
                    _logger.warning(
                        'era_seo.ai_agent_id ICP points at ai.agent #%s which '
                        'no longer exists; falling back to the site Ask-AI '
                        'agent. Re-pick an agent in Settings → ERA SEO → AI '
                        'Auto-Fix.', agent_id)
            except (ValueError, TypeError):
                if agent_id not in _STALE_AGENT_WARNED:
                    _STALE_AGENT_WARNED.add(agent_id)
                    _logger.warning(
                        'era_seo.ai_agent_id ICP is not a valid integer (%r); '
                        'falling back to the site Ask-AI agent.', agent_id)
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
        """One agent round-trip for a finding fix in one language (or none).

        When a target language is given and the model's first response is in
        the wrong language script (e.g. English when we asked for Arabic),
        we retry ONCE with a hardened system_prompt that screams the target
        language. The retry is suppressed for slugs (lang is None) and for
        languages we can't script-detect cheaply.
        """
        ctx_record = target_record.with_context(lang=lang.code) if lang else target_record
        prompt = self._build_prompt(finding, ctx_record, field, lang=lang)
        response = agent.get_direct_response(prompt=prompt, context_message=SEO_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_json(raw)
        if 'proposed_value' not in parsed:
            raise ValueError(_('AI response missing "proposed_value".'))
        if lang is not None and not self._looks_like_language(
                parsed.get('proposed_value', ''), lang.code):
            _logger.warning(
                'AI suggest_fix: response for %s [%s] looks like the wrong '
                'language — retrying with a hardened prompt. First reply: %r',
                field, lang.code, parsed.get('proposed_value', '')[:80])
            stricter_ctx = SEO_CONTEXT + (
                '\n\nCRITICAL OVERRIDE: respond in {name} ({code}) only. '
                'The previous response was rejected for being in the wrong '
                'language. Write every value in {name}; do not echo the '
                'page content\'s language.'.format(
                    name=lang.name or lang.code, code=lang.code)
            )
            response = agent.get_direct_response(
                prompt=prompt, context_message=stricter_ctx)
            retry_raw = response[0] if response else ''
            retry_parsed = self._parse_json(retry_raw)
            if 'proposed_value' in retry_parsed and self._looks_like_language(
                    retry_parsed.get('proposed_value', ''), lang.code):
                parsed = retry_parsed
        return parsed

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

        # Language-script validation + one retry, same shape as _call_finding.
        # Concatenate the produced field values so we judge on the actual
        # text the user will see, not just one field.
        if lang is not None:
            sample = ' '.join(
                str(parsed.get(name) or '') for name in names if parsed.get(name)
            ).strip()
            if sample and not self._looks_like_language(sample, lang.code):
                _logger.warning(
                    'AI fill_seo: response for %s [%s] looks like the wrong '
                    'language — retrying with a hardened prompt. First reply: %r',
                    record._name, lang.code, sample[:80])
                stricter_ctx = FILL_CONTEXT + (
                    '\n\nCRITICAL OVERRIDE: respond in {name} ({code}) only. '
                    'The previous response was rejected for being in the wrong '
                    'language. Write every value in {name}; do not echo the '
                    'page content\'s language.'.format(
                        name=lang.name or lang.code, code=lang.code)
                )
                response = agent.get_direct_response(
                    prompt=prompt, context_message=stricter_ctx)
                retry_raw = response[0] if response else ''
                try:
                    retry_parsed = self._parse_fill_json(retry_raw, names)
                except ValueError:
                    retry_parsed = None
                if retry_parsed:
                    retry_sample = ' '.join(
                        str(retry_parsed.get(n) or '') for n in names
                        if retry_parsed.get(n)
                    ).strip()
                    if retry_sample and self._looks_like_language(
                            retry_sample, lang.code):
                        parsed = retry_parsed
                        raw = retry_raw

        return {
            'fields': {name: (parsed.get(name) or '') for name in names},
            'explanation': parsed.get('explanation', ''),
            'confidence': float(parsed.get('confidence', 0.0)),
            'model': agent.llm_model or _('AI agent'),
            'raw_json': raw,
            'lang': lang.code if lang else (self.env.lang or 'en_US'),
        }

    def pick_schema(self, record, templates):
        """Ask the AI to choose the most appropriate JSON-LD schema template
        for ``record`` from ``templates``.

        :param record:    a record carrying era.seo.mixin (any page-ish thing)
        :param templates: recordset of era.seo.schema.template to choose from
        :returns: dict ``{'code': '<chosen template.code>', 'reason': str,
                          'confidence': float}``. Caller validates `code` is
                  in the supplied set.
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        if not templates:
            raise ValueError(_('No schema templates available for AI to pick from.'))

        agent = self._resolve_agent()
        prompt = self._build_pick_schema_prompt(record, templates)
        response = agent.get_direct_response(
            prompt=prompt, context_message=PICK_SCHEMA_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_pick_schema_json(raw, [t.code for t in templates])
        return parsed

    @classmethod
    def _build_pick_schema_prompt(cls, record, templates):
        excerpt, h1, detected = cls._extract_page_signal(record)
        url = getattr(record, 'url', None) or record._get_seo_path() or '/'
        template_lines = []
        for t in templates:
            desc = (t.description or t.schema_type or '').replace('\n', ' ').strip()
            template_lines.append('  - {code} ({stype}): {desc}'.format(
                code=t.code, stype=t.schema_type, desc=desc[:200]))
        return (
            'INPUT:\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '  detected_lang: {detected}\n'
            'AVAILABLE TEMPLATES (pick ONE by code):\n'
            '{templates}\n'
            'OUTPUT (one JSON object, exactly these keys):\n'
            '  {{"code": "<one of the codes above>", '
            '"reason": "<one short sentence>", '
            '"confidence": <0.0-1.0>}}'.format(
                url=url,
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'")[:1500],
                detected=detected or 'unknown',
                templates='\n'.join(template_lines),
            )
        )

    @classmethod
    def _parse_pick_schema_json(cls, raw, valid_codes):
        parsed = cls._parse_json(raw)
        code = (parsed.get('code') or '').strip()
        if code not in valid_codes:
            raise ValueError(
                _('AI returned schema code %r which is not in the available set.', code))
        return {
            'code': code,
            'reason': str(parsed.get('reason') or '').strip(),
            'confidence': float(parsed.get('confidence') or 0.0),
        }

    def propose_article(self, business_context, past_titles, existing_categories,
                        lang_code=None, trending_now=None, prompt_addendum=None):
        """Ask the AI agent to propose a fresh, trend-aware blog article for
        the site.

        :param business_context: dict-ish — at least 'org_name' and 'summary'
                                 describing what the site is about.
        :param past_titles: iterable of recently-published article titles so
                            the AI avoids restating them.
        :param existing_categories: iterable of category names — encourages
                                    reuse over proliferation.
        :param lang_code: language to write the article in (e.g. 'ar_001').
                          When None, the agent picks based on the site context.
        :param trending_now: iterable of currently-trending search queries
                             (e.g. from Google Trends). The agent prefers one
                             of them when relevant.
        :param prompt_addendum: free-form admin guidance appended to the
                                prompt under an "ADMIN GUIDANCE" section.
                                None / empty = use defaults.
        :returns: dict ``{'title', 'subtitle', 'content_html', 'seo_title',
                          'seo_description', 'seo_keywords', 'category',
                          'image_prompt', 'trend_signal', 'reason',
                          'confidence'}``.
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        agent = self._resolve_agent()
        prompt = self._build_article_prompt(
            business_context, past_titles, existing_categories,
            lang_code, trending_now, prompt_addendum)
        response = agent.get_direct_response(
            prompt=prompt, context_message=ARTICLE_CONTEXT)
        raw = response[0] if response else ''
        # Refusal detection. When the model declines (often because a
        # trends item is sensitive — politics, public figures, breaking
        # news — or the admin's addendum tripped a safety rail) the body
        # is plain prose ("I'm sorry, I can't…") instead of JSON. Retry
        # once with a neutral prompt: no trend signal, no addendum, so
        # the agent picks its own safe topic.
        if self._looks_like_refusal(raw):
            _logger.info(
                'propose_article: agent refused (likely due to trend or '
                'addendum). Retrying with a neutral prompt. First response: %r',
                (raw or '')[:200])
            neutral_prompt = self._build_article_prompt(
                business_context, past_titles, existing_categories,
                lang_code, trending_now=None, prompt_addendum=None)
            response = agent.get_direct_response(
                prompt=neutral_prompt, context_message=ARTICLE_CONTEXT)
            raw = response[0] if response else ''
        parsed = self._parse_json(raw)
        for k in ('title', 'content_html'):
            if not (parsed.get(k) or '').strip():
                raise ValueError(_('AI article proposal is missing %r.', k))
        # Word-count enforcement: 600 floor.
        # Up to two extension passes — each takes the current best draft
        # and asks the agent to EXTEND it (not rewrite). Extending is
        # easier on the model than "rewrite longer" and avoids losing
        # good content from earlier passes. We keep the longest draft
        # we've seen.
        wc = self._count_words(parsed.get('content_html', ''))
        for attempt in range(1, 3):
            if wc >= 600:
                break
            _logger.info(
                'propose_article: draft is %d words (< 600); extension '
                'pass %d/2', wc, attempt)
            extend_prompt = (
                'Below is the current draft article. It is %d words; the '
                'editorial floor is 600 words (target 700-1000). Return '
                'the SAME JSON shape, but with `content_html` EXTENDED to '
                'at least 700 words by ADDING substantive material: '
                'concrete examples, named tools / vendors / numbers, '
                'specific Saudi-market context if relevant, a short FAQ '
                'block, or a step-by-step section. Do not delete or '
                'paraphrase existing content; preserve title, '
                'trend_signal, category, image_prompt, and seo meta.\n\n'
                'CURRENT DRAFT (as JSON):\n%s'
            ) % (wc, json.dumps(parsed, ensure_ascii=False))
            try:
                response = agent.get_direct_response(
                    prompt=extend_prompt, context_message=ARTICLE_CONTEXT)
                raw2 = response[0] if response else ''
                parsed2 = self._parse_json(raw2)
                wc2 = self._count_words(parsed2.get('content_html', ''))
            except (ValueError, Exception):  # noqa: BLE001
                # Malformed retry — stop trying, keep the best so far.
                break
            if (parsed2.get('content_html') or '').strip() and wc2 > wc:
                parsed = parsed2
                wc = wc2
        if wc < 600:
            _logger.warning(
                'propose_article: final draft only %d words (floor is 600) '
                'after retries; publishing anyway — review and extend '
                'manually if needed', wc)
        return {
            'title':           parsed.get('title', '').strip(),
            'subtitle':        parsed.get('subtitle', '').strip(),
            'excerpt':         parsed.get('excerpt', '').strip(),
            'content_html':    parsed.get('content_html', '').strip(),
            'seo_title':       parsed.get('seo_title', '').strip(),
            'seo_description': parsed.get('seo_description', '').strip(),
            'seo_keywords':    parsed.get('seo_keywords', '').strip(),
            'category':        parsed.get('category', '').strip(),
            'image_prompt':    parsed.get('image_prompt', '').strip(),
            'trend_signal':    parsed.get('trend_signal', '').strip(),
            'reason':          parsed.get('reason', '').strip(),
            'confidence':      float(parsed.get('confidence') or 0.0),
        }

    @classmethod
    def _build_article_prompt(cls, business_context, past_titles, existing_categories,
                              lang_code=None, trending_now=None, prompt_addendum=None):
        trends_block = (
            '  trending_now (Google Trends, daily, for the configured geo): {t}\n'
            .format(t=json.dumps(list(trending_now)[:15], ensure_ascii=False))
            if trending_now else ''
        )
        addendum_block = (
            'ADMIN GUIDANCE (highest-priority, overrides anything else when in conflict):\n'
            '{a}\n\n'.format(a=str(prompt_addendum).strip())
            if prompt_addendum else ''
        )
        return (
            '{addendum}'
            'INPUT:\n'
            '  business_name: "{name}"\n'
            '  business_summary: "{summary}"\n'
            '  target_language: {lang}\n'
            '  recent_post_titles: {past}\n'
            '  existing_categories: {cats}\n'
            '{trends}'
            'TASK:\n'
            '  1. If trending_now has an item genuinely relevant to this '
            'business and audience, pick that as your trend signal. Otherwise '
            'identify another current or emerging trend in the domain. Be '
            'specific (a concrete tool, behaviour, event, or shift), not '
            'generic ("AI is changing everything"). Surface the chosen signal '
            'in `trend_signal`.\n'
            '  2. Write a complete article on that topic that the business '
            'could publish today. Substantive — AT LEAST 600 words of HTML, '
            'target 700-1000 words. Count words in the body text only.\n'
            '  2a. STRUCTURE — `content_html` MUST contain, in order: an intro '
            'paragraph (3-5 sentences); FIVE to SEVEN named sections each '
            'headed with <h2> and each containing at least TWO substantive '
            'paragraphs (no single-sentence sections); a short closing '
            'paragraph. Use <ul>/<li> where it adds value. This structure '
            'is what the 600-word floor depends on — skipping sections '
            'will undershoot.\n'
            '  3. Fill SEO meta and an image_prompt that an image-generation '
            'model could use as-is.\n'
            'OUTPUT (one JSON object, exactly these keys):\n'
            '  {{"title": "<=70 chars", "subtitle": "<=140 chars (or empty)", '
            '"excerpt": "1-2 sentence plain-text summary for blog list '
            'cards and RSS, <=300 chars, no HTML", '
            '"content_html": "<full article body as HTML, no <html>/<body> '
            'wrappers, allow <h2>, <h3>, <p>, <ul>, <li>, <strong>, <em>, <a>>", '
            '"seo_title": "<=60 chars", "seo_description": "140-160 chars", '
            '"seo_keywords": "3-6 comma-separated terms", '
            '"category": "<existing category if it fits, else a short new name>", '
            '"image_prompt": "<one paragraph describing a hero image for this '
            'article — concrete, no text overlay>", '
            '"trend_signal": "<one sentence on the trend you picked and why now>", '
            '"reason": "<one sentence on why this fits the business>", '
            '"confidence": <0.0-1.0>}}'.format(
                addendum=addendum_block,
                name=str(business_context.get('org_name') or '').replace('"', "'"),
                summary=str(business_context.get('summary') or '').replace('"', "'")[:600],
                lang=lang_code or 'auto-detect from business_summary',
                past=json.dumps(list(past_titles)[:30], ensure_ascii=False),
                cats=json.dumps(list(existing_categories)[:30], ensure_ascii=False),
                trends=trends_block,
            )
        )

    def pick_blog_taxonomy(self, post):
        """Ask the AI for a category (required) and series (optional) for
        ``post`` based on its content.

        :returns: dict ``{'category': str, 'series': str, 'reason': str,
                          'confidence': float}``. `series` may be empty.
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        agent = self._resolve_agent()
        prompt = self._build_blog_taxonomy_prompt(post)
        response = agent.get_direct_response(
            prompt=prompt, context_message=BLOG_TAXONOMY_CONTEXT)
        raw = response[0] if response else ''
        parsed = self._parse_json(raw)
        category = (parsed.get('category') or '').strip()
        if not category:
            raise ValueError(_('AI did not return a category.'))
        series = (parsed.get('series') or '').strip()
        return {
            'category': category,
            'series': series if series.lower() not in ('', 'none', 'null', 'n/a') else '',
            'reason': str(parsed.get('reason') or '').strip(),
            'confidence': float(parsed.get('confidence') or 0.0),
        }

    @classmethod
    def _build_blog_taxonomy_prompt(cls, post):
        excerpt, h1, detected = cls._extract_page_signal(post)
        url = getattr(post, 'url', None) or post._get_seo_path() or '/'
        # Surface existing taxonomy so the AI prefers reuse over proliferation.
        env = post.env
        existing_cats = env['era.blog.category'].sudo().search([], limit=50).mapped('name')
        existing_series = env['era.blog.series'].sudo().search([], limit=50).mapped('name')
        return (
            'INPUT:\n'
            '  url: {url}\n'
            '  post_h1: "{h1}"\n'
            '  post_excerpt: "{excerpt}"\n'
            '  detected_lang: {detected}\n'
            '  existing_categories: {cats}\n'
            '  existing_series: {series}\n'
            'OUTPUT (one JSON object, exactly these keys):\n'
            '  {{"category": "<reuse existing if it fits, else propose a new short '
            'name>", "series": "<empty unless the post is clearly part of a '
            'multi-part series>", "reason": "<one short sentence>", '
            '"confidence": <0.0-1.0>}}'.format(
                url=url,
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'")[:1500],
                detected=detected or 'unknown',
                cats=json.dumps(existing_cats[:20], ensure_ascii=False),
                series=json.dumps(existing_series[:20], ensure_ascii=False),
            )
        )

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

    # Phrases the major LLM providers use when refusing a request.
    # Lower-cased; the check is substring against the lower-cased response.
    _REFUSAL_MARKERS = (
        "i'm sorry, i can't",
        "i am sorry, i can't",
        "i can't assist",
        "i cannot assist",
        "i'm not able to",
        "i am not able to",
        "i won't be able to",
        "i can't help with that",
        "i cannot help with that",
        'i can’t assist',   # curly apostrophe
        'i can’t help',
        "as an ai",  # usually accompanies a refusal
    )

    @classmethod
    def _looks_like_refusal(cls, raw):
        """True when the response looks like a content-policy refusal —
        a plain-prose apology instead of the requested JSON.

        Conservative: only fires when the body doesn't parse as JSON
        (so a JSON response containing the word "sorry" inside content
        won't false-positive).
        """
        text = (raw or '').strip()
        if not text:
            return False
        # If it parses as JSON we accept it — refusals are never JSON.
        try:
            json.loads(text)
            return False
        except (json.JSONDecodeError, TypeError):
            pass
        # Try the fenced/embedded variants the parser handles too.
        try:
            cls._parse_json(text)
            return False
        except ValueError:
            pass
        lower = text.lower()
        return any(marker in lower for marker in cls._REFUSAL_MARKERS)

    @staticmethod
    def _count_words(html):
        """Strip tags from `html` and return the word count of the body.
        Cheap regex-based stripper — accurate enough for the >= 600 floor;
        does not try to handle entities or nested HTML edge cases.
        """
        if not html:
            return 0
        # 1. Drop tags. 2. Collapse whitespace. 3. Count non-empty tokens.
        text = re.sub(r'<[^>]+>', ' ', html)
        tokens = [t for t in re.split(r'\s+', text) if t.strip()]
        return len(tokens)

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
            # No _() wrapper here: this helper is a @staticmethod called
            # from many paths including crons, where the call frame has no
            # env/uid for Odoo's translation system to walk to. Wrapping
            # forces _get_lang() to log a "no translation language detected"
            # WARNING with a multi-line stack trace per refused JSON. The
            # message reaches users (if at all) via the cron's WARNING line
            # and not via UI, so plain English is fine.
            raise ValueError(
                'AI returned output that is not valid JSON: %s'
                % (raw or '')[:200]
            ) from exc
        # NOTE: the legacy suggest_fix contract requires `proposed_value`,
        # but every other caller uses a different JSON shape (propose_article:
        # title/content_html; pick_schema: code; pick_blog_taxonomy: category;
        # fill_seo: per-field names). Callers that need `proposed_value`
        # validate it themselves — see `_field_and_mechanical_fix`.
        return parsed

    @classmethod
    def _build_prompt(cls, finding, target, field, lang=None):
        excerpt, h1, detected = cls._extract_page_signal(target)
        current = (target.url or '') if field == 'url' else (getattr(target, field, None) or '')
        # We surface the target language THREE times — at the top of the
        # prompt (where the model anchors its plan), inline in the INPUT
        # block, and again at the very end right before OUTPUT. Models
        # given a long English excerpt routinely echo English even when
        # the per-language fill is for Arabic; the triple-anchor mostly
        # stops that. The empty-string for non-translatable fields means
        # this is a no-op for slugs.
        lang_header = cls._lang_header(lang)
        lang_footer = cls._lang_footer(lang)
        return (
            '{lang_header}'
            'INPUT:\n'
            '  defect: {code}\n'
            '  url: {url}\n'
            '  current_value: {current}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '{lang_line}'
            '{lang_footer}'
            'OUTPUT:'.format(
                code=finding.check_code,
                url=finding.url or '/',
                current=json.dumps(current) if current else 'null',
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                lang_header=lang_header,
                lang_line=cls._lang_line(lang, detected),
                lang_footer=lang_footer,
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
            '{lang_header}'
            'INPUT:\n'
            '  mode: {mode}\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '{current}\n'
            '  page_excerpt: "{excerpt}"\n'
            '{lang_line}'
            '{lang_footer}'
            'FIELDS TO PRODUCE (return each as a JSON key with the same name):\n'
            '{produce}\n'
            'OUTPUT (one JSON object, exactly these keys):\n'
            '  {shape}'.format(
                mode='rewrite all' if overwrite else 'fill missing',
                url=url,
                h1=h1.replace('"', "'"),
                current='\n'.join(current_lines),
                excerpt=excerpt.replace('"', "'"),
                lang_header=cls._lang_header(lang),
                lang_line=cls._lang_line(lang, detected),
                lang_footer=cls._lang_footer(lang),
                produce='\n'.join(produce_lines),
                shape=shape,
            )
        )

    @staticmethod
    def _lang_line(lang, detected):
        """Emit the inline target-language instruction in the INPUT block.

        When ``lang`` is given (multi-language generation), the output MUST be
        in that language regardless of the page content's language — we are
        producing the per-language translation of the field. When it's None,
        fall back to the heuristically detected language.
        """
        if lang is not None:
            name = lang.name or lang.code
            return (
                '  target_language: {name} ({code})\n'
                '  HARD REQUIREMENT: write EVERY output value in {name}. The page '
                'content may be in another language — compose/translate in {name} '
                'regardless; never answer in the content\'s language.\n'.format(
                    name=name, code=lang.code,
                )
            )
        return '  language_hint: {}\n'.format(detected)

    @staticmethod
    def _lang_header(lang):
        """Prepend a banner line to the prompt so the model anchors its plan
        on the target language before it reads the (often English) excerpt.
        Returns '' when no per-language instruction is needed.
        """
        if lang is None:
            return ''
        name = lang.name or lang.code
        return (
            '### LANGUAGE-FIRST ###\n'
            'You MUST respond in {name} ({code}) only. The page content '
            'below may be in a DIFFERENT language; translate or compose '
            'in {name} regardless. Outputs in any other language will be '
            'rejected.\n\n'.format(name=name, code=lang.code)
        )

    @staticmethod
    def _lang_footer(lang):
        """Trail the prompt with one final reminder, right before OUTPUT.

        Three anchors total (header, inline, footer) — empirically necessary
        for cheap models that otherwise echo whatever language they read.
        """
        if lang is None:
            return ''
        name = lang.name or lang.code
        return (
            'FINAL CHECK before you answer:\n'
            '  - Is every output value written in {name}? If not, REWRITE in {name}.\n'
            '  - Do NOT translate the JSON keys — only the values.\n\n'.format(
                name=name)
        )

    @staticmethod
    def _lang_prefix(lang_code):
        """Return the ISO 639-1 two-letter prefix of a locale code.

        Odoo stores language codes as locale strings: ``ar_001`` (world
        Arabic), ``ar_SA`` (Saudi), ``ar_EG`` (Egyptian), ``en_US``,
        ``en_GB``, ``fr_FR``, ``fr_CA``, ``zh_CN``, ``zh_TW`` and so on.

        Every language-matching check in this module should compare the
        2-letter prefix instead of the full locale — a literal compare to
        ``'ar_001'`` would silently miss every other Arabic locale, and
        nothing in our flow cares about regional variants. Centralising
        the slice here makes the convention enforceable and obvious.

        Returns the lowercased two-character prefix, or ``''`` when the
        input is empty / shorter than two characters.
        """
        if not lang_code:
            return ''
        return lang_code[:2].lower()

    @staticmethod
    def _looks_like_language(text, lang_code):
        """Cheap heuristic: does ``text`` look like it's in ``lang_code``?

        Used as a post-validation gate after a per-language AI call. We don't
        try to be linguistically rigorous — just count which Unicode script
        dominates and reject when the dominant script clearly doesn't match
        the requested language. Returns False only when we're confident the
        response is in the wrong language.

        Currently distinguishes Arabic (U+0600-U+06FF + U+0750-U+077F) from
        Latin scripts. Everything else (Cyrillic, CJK, …) is allowed through
        without judgement; the worst case is a missed retry, not a false
        rejection.

        Locale handling: comparisons go through ``_lang_prefix`` so any
        Arabic locale (ar, ar_001, ar_SA, ar_EG, ar-EG, AR_001, …) hits
        the Arabic branch, not just the one Odoo happens to ship by default.
        """
        if not text or not lang_code:
            return True
        # Tally Arabic vs Latin letters; ignore digits/punct/whitespace.
        arabic = 0
        latin = 0
        for ch in text:
            cp = ord(ch)
            if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F:
                arabic += 1
            elif ('a' <= ch <= 'z') or ('A' <= ch <= 'Z'):
                latin += 1
        total = arabic + latin
        if total < 5:
            # Too few letters to judge (e.g. just a slug). Pass.
            return True
        prefix = AIClient._lang_prefix(lang_code)
        if prefix == 'ar':
            # Arabic should dominate.
            return arabic >= max(latin, 1) * 2
        # Default: anything not Arabic-tagged should NOT be majority Arabic.
        return arabic <= total * 0.3

    @staticmethod
    def _extract_page_signal(record):
        """Return (excerpt, h1, language_hint) from a record's content."""
        from lxml import html as lxml_html
        content_html = (getattr(record, 'content', None)
                        or getattr(record, 'arch', None)
                        or getattr(record, 'content_html', None) or '')
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

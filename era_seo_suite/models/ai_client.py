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

# ---------------------------------------------------------------------------
# JSON repair primitives.
# Cheap models emit JSON with two recurring, non-recoverable defects: a stray
# chat-template end token appended after the object (</assistant>, <|im_end|>,
# </s>, ...) and an occasional unquoted string value. The pipeline is: extract
# the first brace-balanced object (drops leading prose AND a trailing token) ->
# strict decode (Layer A, lossless, never touches a valid response) -> only on
# failure, a conservative repair pass (Layer B: strip control tokens, trailing
# commas, quote-repair) re-validated by the strict decoder. Because repair runs
# ONLY after strict decode fails and is re-validated, valid JSON is never
# mutated and a bad repair fails closed (raises) rather than returning wrong data.
# ---------------------------------------------------------------------------

# Allow-list of chat/control end tokens. Two hard rules learned the hard way:
#   - Match EXACT tags only (`</?name>` with an immediate close), never
#     `name\b[^>]*>`, so real HTML like <system-status> or a tag with
#     attributes is left alone.
#   - Single-letter `s` is DELIBERATELY EXCLUDED: <s>/</s> are HTML5
#     strikethrough and must survive inside JSON string values (e.g. a struck
#     old price "Was <s>SAR 500</s> now SAR 350"). The brace-balanced span
#     extractor already drops a trailing </s>/<|im_end|> EOS token anyway.
# This stripper is applied ONLY in the Layer-B repair pass (after a strict
# decode has already failed), so it can never mutate valid JSON.
_CTRL_TOKEN_RE = re.compile(
    r'</?(?:assistant|user|system|im_start|im_end|eot_id|eot|end_of_turn)>'
    r'|<\|[^|>]{1,40}\|>',
    re.IGNORECASE,
)


def _strip_ctrl_tokens(text):
    return _CTRL_TOKEN_RE.sub('', text)


def _extract_json_object_span(text):
    """Return the first brace-balanced ``{...}`` object in ``text``.

    Scans from the first ``{`` honoring string/escape state so a brace inside
    a quoted value (e.g. an Arabic title) never closes the object early. On
    unbalanced/truncated input returns the remainder from the first ``{`` so a
    repair pass can still attempt it. Returns ``text`` unchanged if no ``{``.
    """
    start = text.find('{')
    if start < 0:
        return text
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    # Unbalanced / truncated — hand back the remainder for the repair pass.
    return text[start:]


def _repair_json_text(text):
    """Best-effort repair of common cheap-model JSON defects. Conservative:
    only transformations that cannot turn one valid value into a different
    valid one. Runs ONLY after strict decode has already failed, and the
    output is always re-validated by a strict decoder upstream."""
    # 0. Strip stray chat/control end tokens (</assistant>, <|im_end|>, ...).
    #    Done HERE — not before the strict decode — so a valid response whose
    #    values legitimately contain such substrings is never mutated.
    text = _strip_ctrl_tokens(text)
    # 1. Trailing comma before a closer: {"a": 1,} / [1, 2,]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # 2. Unquoted string value: `"key": bareword text"` -> `"key": "bareword text"`.
    #    Anchored to a value that STARTS with a char that cannot begin a JSON
    #    literal (not a quote/brace/bracket/digit/sign, not lower-case t/f/n
    #    for true/false/null) and ENDS at a closing quote right before , } ].
    #    The [^"\n] class forbids spanning an existing quote, so it can never
    #    swallow the following key.
    text = re.sub(
        r'(:\s*)(?![\s"\d\[\{tfn\-])([^"\n]*?")(\s*[,}\]])',
        r'\1"\2\3',
        text,
    )
    return text


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
business. Optimize for helpful, reliable, people-first content: satisfy a real \
reader intent first, then make the page easy for search engines to understand. \
Pick a topic that is current only when it is genuinely relevant to this business \
and audience; never chase a trend for traffic alone. Surface the chosen signal \
in `trend_signal`. Do NOT repeat any title from `recent_post_titles`. Write the \
article in `target_language` (or pick from `business_summary` if it says \
'auto-detect'). The body must be substantive, factual, and grounded — no \
hallucinated statistics, fake quotes, unverifiable claims, or keyword stuffing. \
Reply with ONE JSON object exactly matching the OUTPUT shape. No markdown fences, \
no prose around the JSON.
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


# Stable taxonomy of GEO content dimensions the AI review can flag. The codes
# double as audit-finding check codes (prefix geo_ai_), so they must stay stable.
GEO_REVIEW_CODES = (
    'geo_ai_answer_summary',
    'geo_ai_specificity',
    'geo_ai_question_headings',
    'geo_ai_proof',
    'geo_ai_comparison',
    'geo_ai_entity_clarity',
    'geo_ai_language_register',
)

# Context for the AI GEO content review. The model judges ONE page on the fixed
# dimensions above and returns only the ones that are genuinely a problem, each
# with a concrete, page-specific recommendation.
GEO_REVIEW_CONTEXT = """IMPORTANT: for THIS task, ignore any earlier output-format \
instruction. You are a GEO (Generative Engine Optimization) reviewer judging how well \
ONE web page can be understood, extracted, and CITED by AI answer engines (ChatGPT, \
Perplexity, Google AI Overviews, Gemini) so the site is recommended as a source.

Judge the page ONLY on these fixed dimensions (use the exact code):
  - geo_ai_answer_summary: a concise, factual answer/definition near the top that \
directly states what the page/business is (who, what, where).
  - geo_ai_specificity: concrete, verifiable facts (numbers, dates, %, prices, named \
entities) versus vague, unquantified marketing claims.
  - geo_ai_question_headings: section headings phrased as the real questions users ask \
(cost, timeline, how, comparisons), not just marketing slogans.
  - geo_ai_proof: extractable TEXT proof — case studies / testimonials with specifics \
(client, industry, measurable result) — not image-only logos.
  - geo_ai_comparison: a comparison table or structured comparison where the topic \
invites one (vs alternatives, tiers, editions).
  - geo_ai_entity_clarity: an unambiguous statement of organization identity, location, \
credentials, and who it serves.
  - geo_ai_language_register: substantive/answer content written in clear standard \
language an engine quotes well (for Arabic, Modern Standard Arabic) — not only \
colloquial dialect.

Reply with ONE JSON object and nothing else — no markdown:
  {"issues": [{"code": "<one code>", "severity": "info"|"warning", \
"title": "<short>", "detail": "<why it hurts AI citation, 1-2 sentences>", \
"recommendation": "<specific, actionable fix for THIS page>"}], \
"summary": "<one sentence>"}

Rules:
- Include a dimension in "issues" ONLY when it is genuinely a problem on this page; \
omit dimensions that are already good.
- Every recommendation must be concrete and specific to THIS page's topic, never generic.
- Write title / detail / recommendation in the page's own language (Arabic page → Arabic).
- Never invent facts about the business.
- severity: "warning" when it materially blocks citation; otherwise "info"."""


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

    _ARTICLE_MIN_WORDS = 900
    _ARTICLE_TARGET_WORDS = '1000-1400'
    _ARTICLE_MAX_LINK_TARGETS = 16
    # Wall-clock budget for the whole propose_article call (initial draft +
    # refusal retry + word-floor extension passes). Each LLM round-trip on a
    # 1000+ word article can take minutes; chaining 3-4 of them used to blow
    # past the worker's limit_time_real (1200s) and get the process force-killed
    # mid-article. We stop entering new extension passes once this budget is
    # spent and publish the best draft so far. Kept well under 1200s so the
    # post-steps (image, category, create) still finish inside the worker limit.
    _ARTICLE_GEN_BUDGET_S = 540

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
        EVERY installed website language **in a single AI call** — the
        model returns one JSON object with per-language values plus a
        single explanation/confidence. The non-translatable slug (``url``)
        is generated once.

        Per-language script validation is still applied to the returned
        values: any language whose value is in the wrong script triggers
        ONE retry (one consolidated retry call covering only the failed
        languages, not one retry per language). This caps the worst-case
        call count at 2 per finding instead of 2N.
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

        # Translatable text field — ONE call covering every installed
        # language at once. See `_call_finding_multilang` for the
        # consolidated-prompt + per-language retry shape.
        languages, default_lang = self._record_languages(target_record)
        # If the finding is scoped to one language (per-language audit), fix
        # only that language so we don't overwrite good translations.
        finding_lang = getattr(finding, 'lang_id', False)
        if finding_lang:
            languages = finding_lang
            default_lang = finding_lang
        translations, explanation, confidence = \
            self._call_finding_multilang(
                agent, finding, target_record, field, languages, default_lang)

        default_code = (default_lang.code if default_lang
                        else (languages[:1].code if languages else self.env.lang))
        primary = translations.get(default_code) or next(iter(translations.values()), '')
        return self._result(
            'field', field=field, translations=translations,
            proposed_value=primary, explanation=explanation,
            confidence=confidence,
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
        # The page is rendered/served in the website's DEFAULT language, so read
        # (and later write) alt text in THAT language version, not the admin's UI
        # language. The /team avatars, for instance, have alt in en_US but not in
        # the served ar_001 arch — exactly the 8 the audit flags. Reading the
        # admin-language arch would miss them and falsely report "nothing to fix".
        _languages, default_lang = self._record_languages(target_record)
        lang_code = default_lang.code if default_lang else None
        lang_record = (target_record.with_context(lang=lang_code)
                       if lang_code else target_record)
        # Editable images live in the page's own content/arch (in the served
        # language). The audit also flags images a crawler sees in the RENDERED
        # page (theme / dynamic snippets) that aren't in any arch language and
        # therefore can't be fixed from here.
        images = self._images_without_alt(lang_record)
        rendered_total = self._rendered_missing_alt(target_record)
        if not images:
            if rendered_total:
                raise ValueError(_(
                    "All %d image(s) without alt text on this page are rendered "
                    "by the theme or a dynamic snippet — not the page's own "
                    "editable content — so they can't be fixed from here. Add the "
                    "alt text at the source (the snippet template or the linked "
                    "image/record).", rendered_total))
            raise ValueError(_('No images without alt text were found on the page.'))

        _excerpt, topic, _detected = self._extract_page_signal(lang_record)
        ai_alts, explanation, confidence, model = None, '', 0.0, 'mechanical'
        try:
            agent = self._resolve_agent()
            prompt = self._build_alt_prompt(lang_record, images)
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

        # Be honest about images the audit sees in the rendered page but that we
        # can't edit from here (theme / dynamic-snippet images live outside arch).
        if rendered_total > len(images):
            explanation = (explanation + ' ' + _(
                '%d more image(s) on this page are rendered by the theme or a '
                'dynamic snippet and must be fixed at the source.',
                rendered_total - len(images))).strip()

        preview = '\n'.join(
            '{} → {}'.format((p['src'] or '(no src)')[:50], p['alt']) for p in pairs
        )
        return self._result(
            'image_alt', field='content', proposed_value=preview,
            payload={'alts': pairs, 'lang': lang_code},
            explanation=explanation, confidence=confidence, model=model,
        )

    def _fix_thin_content(self, finding, target_record):
        """AI proposes an HTML block to append, IN THE WEBSITE'S DEFAULT
        LANGUAGE; confidence is capped for review.

        The page signal and the generated content both use the website default
        language (e.g. ar_001), not the caller's context language — on an
        Arabic-primary site the en_US version is an empty shell, so reading it
        gave the model no signal and it answered in English. We read the page
        in the default language, name that language in the prompt, and stamp it
        on the payload so Apply writes into the SAME language version.
        """
        agent = self._resolve_agent()
        _langs, default_lang = self._record_languages(target_record)
        lang_code = (default_lang.code if default_lang
                     else (target_record.env.context.get('lang') or 'en_US'))
        lang_name = (default_lang.name if default_lang else lang_code)
        lang_target = target_record.with_context(lang=lang_code)
        prompt = self._build_thin_prompt(lang_target, lang_name)
        response = agent.get_direct_response(
            prompt=prompt, context_message=THIN_CONTENT_CONTEXT)
        parsed = self._parse_html_json(response[0] if response else '')
        html = parsed['html']
        confidence = min(float(parsed.get('confidence', 0.0)), _THIN_CONTENT_MAX_CONFIDENCE)
        return self._result(
            'thin_content', field='content',
            proposed_value=self._html_preview(html),
            payload={'html': html, 'lang': lang_code},
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

    def _call_finding_multilang(self, agent, finding, target_record,
                                field, languages, default_lang):
        """ONE agent call returning per-language values for a translatable field.

        Sends a prompt that lists every target language and asks for one
        JSON object: ``{"by_lang": {"en_US": "...", "ar_001": "..."},
        "explanation": "...", "confidence": 0.0-1.0}``. Cuts AI calls from
        N (one per language) to 1 for a typical title/description fix.

        After the initial call, per-language script validation runs the
        same Unicode-block heuristic as `_call_finding`. If any languages
        come back in the wrong script, ONE consolidated retry call covers
        all of them at once with a hardened context message. The retry
        only replaces individual values whose script now matches; languages
        that pass the first time are kept regardless.

        Returns (translations_dict, explanation_str, confidence_float).
        Confidence is the min across languages — the user reviewing the
        proposal should see the weakest signal.
        """
        prompt = self._build_prompt_multilang(
            finding, target_record, field, languages, default_lang)
        response = agent.get_direct_response(
            prompt=prompt, context_message=SEO_CONTEXT)
        raw = response[0] if response else ''
        try:
            parsed = self._parse_json(raw)
        except ValueError as exc:
            _logger.warning(
                'AI suggest_fix multi-lang: malformed JSON on first attempt '
                'for %s — retrying with hardened JSON discipline. err=%s',
                field, str(exc)[:120])
            stricter_ctx = SEO_CONTEXT + (
                '\n\nCRITICAL JSON DISCIPLINE: the previous response was '
                'rejected for being invalid JSON. Output ONE JSON object, '
                'nothing else — no prose, no markdown fence, no trailing '
                'text. Escape every embedded quote and backslash in '
                'values. Validate the JSON parses before sending.'
            )
            response = agent.get_direct_response(
                prompt=prompt, context_message=stricter_ctx)
            raw = response[0] if response else ''
            parsed = self._parse_json(raw)
        translations = self._extract_multilang_translations(parsed, languages)
        if not translations:
            raise ValueError(
                _('AI response missing per-language values for %s.', field))

        explanation = parsed.get('explanation', '') or ''
        try:
            confidence = float(parsed.get('confidence', 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        # Per-language script validation. Collect any languages whose
        # returned value doesn't match its requested script — those are
        # candidates for the consolidated retry.
        bad_langs = [
            lang for lang in languages
            if not self._looks_like_language(
                translations.get(lang.code, ''), lang.code)
        ]
        if bad_langs:
            bad_names = ', '.join(l.code for l in bad_langs)
            _logger.warning(
                'AI suggest_fix multi-lang: response for %s had wrong-script '
                'values for [%s] — issuing ONE consolidated retry covering '
                'those languages.', field, bad_names)
            stricter_ctx = SEO_CONTEXT + (
                '\n\nCRITICAL OVERRIDE: the previous response had values in '
                'the wrong language for: {names}. Rewrite the WHOLE JSON; '
                'every language entry must be in its own script. Do not '
                'echo the page content language for these slots.'.format(
                    names=bad_names)
            )
            retry_response = agent.get_direct_response(
                prompt=prompt, context_message=stricter_ctx)
            retry_raw = retry_response[0] if retry_response else ''
            try:
                retry_parsed = self._parse_json(retry_raw)
                retry_translations = self._extract_multilang_translations(
                    retry_parsed, languages)
            except (ValueError, json.JSONDecodeError):
                retry_translations = {}
            # Only swap in the retry values for languages that NOW pass
            # the script check — keep the first-call value for any that
            # still don't (better than nothing for the admin to review).
            for lang in bad_langs:
                retry_val = retry_translations.get(lang.code)
                if retry_val and self._looks_like_language(retry_val, lang.code):
                    translations[lang.code] = retry_val

        return translations, explanation, confidence

    @classmethod
    def _build_prompt_multilang(cls, finding, target, field, languages, default_lang):
        """Build the consolidated multi-language prompt for `_fix_field`.

        Reuses _lang_header / _lang_footer for "language-first" framing,
        but lists EVERY language in the body so the model produces one
        JSON with all translations. The page excerpt is shared — the same
        H1 + body drives every language, just translated differently.
        """
        excerpt, h1, detected = cls._extract_page_signal(target)
        current = (target.url or '') if field == 'url' \
            else (getattr(target, field, None) or '')
        # The default language gets the "primary" label so the model
        # anchors its explanation on that one; the others are translations.
        lang_lines = []
        for lang in languages:
            tag = ' (primary)' if (default_lang and lang == default_lang) else ''
            lang_lines.append('    - "{code}"{tag} — write in {name}'.format(
                code=lang.code, tag=tag, name=(lang.name or lang.code)))
        lang_list = '\n'.join(lang_lines)
        by_lang_shape = ', '.join(
            '"{}": "<value>"'.format(lang.code) for lang in languages)

        # Lead with a header (same trick as `_build_prompt`) so the model
        # commits to multi-language output BEFORE reading the (probably
        # English) page excerpt.
        header = (
            '### LANGUAGE-FIRST ###\n'
            'You will produce one value per language listed below. EACH '
            'value MUST be in its OWN language script. Do not echo the '
            'page content\'s language for languages that differ from it.\n\n'
        )

        return (
            '{header}'
            'INPUT:\n'
            '  defect: {code}\n'
            '  url: {url}\n'
            '  current_value: {current}\n'
            '  page_h1: "{h1}"\n'
            '  page_excerpt: "{excerpt}"\n'
            '  target_languages:\n'
            '{lang_list}\n'
            'FINAL CHECK before you answer:\n'
            '  - Is each by_lang value written in its own language script?\n'
            '  - Do NOT translate the JSON keys; only the values.\n\n'
            'OUTPUT (one JSON object, no markdown):\n'
            '  {{"by_lang": {{{by_lang_shape}}}, '
            '"explanation": "<one sentence>", '
            '"confidence": <0.0-1.0>}}'.format(
                header=header,
                code=finding.check_code,
                url=finding.url or '/',
                current=json.dumps(current) if current else 'null',
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                lang_list=lang_list,
                by_lang_shape=by_lang_shape,
            )
        )

    @staticmethod
    def _extract_multilang_translations(parsed, languages):
        """Pull per-language values from a multi-lang AI response.

        Accepts a few response shapes — the strict contract is
        ``{"by_lang": {code: value, ...}}`` but cheap models sometimes
        flatten to ``{code: value, ...}`` at top level. Handle both,
        return an empty dict when neither shape matches so the caller
        can raise a clean error.
        """
        if not isinstance(parsed, dict):
            return {}
        by_lang = parsed.get('by_lang')
        out = {}
        if isinstance(by_lang, dict):
            for lang in languages:
                val = by_lang.get(lang.code)
                if val and isinstance(val, str) and val.strip():
                    out[lang.code] = val.strip()
        if out:
            return out
        # Tolerant fallback: maybe the model put values at top level.
        for lang in languages:
            val = parsed.get(lang.code)
            if val and isinstance(val, str) and val.strip():
                out[lang.code] = val.strip()
        return out

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

    def fill_seo_multilang(self, record, langs, overwrite=False, field_specs=None):
        """Multi-language variant of `fill_seo` — ONE AI call per record
        instead of one per language.

        :param record: a record carrying era.seo.mixin
        :param langs: iterable of res.lang records to generate in. Must be
                      non-empty; pass a single-item recordset if you only
                      want one language and want this consolidated path
                      anyway.
        :param overwrite: prompt hint for the model ("rewrite all" vs.
                          "fill missing").
        :param field_specs: list of ``{'name', 'rule'}`` describing the
                            fields to produce, same shape as fill_seo.

        :returns: dict with shape::

            {
                'by_lang': {lang_code: {field_name: value, ...}, ...},
                'explanation': '<one sentence>',
                'confidence': <float 0-1>,  # min across langs
                'model': '<llm_model>',
                'raw_json': '<raw response>',
            }

        Per-language script validation + ONE consolidated retry covering
        only the languages whose response was in the wrong script. Cuts
        AI calls from N → 1 (or 2 worst case) for a typical 2-language fill.

        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        langs = list(langs)
        if not langs:
            raise ValueError(_('fill_seo_multilang requires at least one language.'))

        specs = field_specs or self._FALLBACK_FILL_SPECS
        names = [s['name'] for s in specs]
        agent = self._resolve_agent()
        prompt = self._build_fill_prompt_multilang(
            record, overwrite=overwrite, langs=langs, field_specs=specs)
        response = agent.get_direct_response(
            prompt=prompt, context_message=FILL_CONTEXT)
        raw = response[0] if response else ''
        try:
            parsed = self._parse_json(raw)
        except ValueError as exc:
            # Cheap models on long multi-language prompts frequently
            # produce malformed JSON (unescaped quotes inside values,
            # trailing commas, ...). One retry with a JSON-discipline
            # banner usually fixes it. If THAT also fails, the
            # ValueError propagates to the caller (the mixin demotes it
            # to a clean WARNING).
            _logger.warning(
                'AI fill_seo multi-lang: malformed JSON on first attempt '
                'for %s — retrying with hardened JSON discipline. err=%s',
                record._name, str(exc)[:120])
            stricter_ctx = FILL_CONTEXT + (
                '\n\nCRITICAL JSON DISCIPLINE: the previous response was '
                'rejected for being invalid JSON. Output ONE JSON object, '
                'nothing else — no prose, no markdown fence, no trailing '
                'text. Escape every embedded quote and backslash in '
                'values. Validate the JSON parses before sending.'
            )
            response = agent.get_direct_response(
                prompt=prompt, context_message=stricter_ctx)
            raw = response[0] if response else ''
            parsed = self._parse_json(raw)
        by_lang = self._extract_multilang_fields(parsed, langs, names)
        if not by_lang:
            raise ValueError(
                _('AI multi-language fill response was missing per-language fields.'))

        # Per-language script validation. Build a sample from each
        # language's concatenated field values; flag mismatched languages.
        bad_langs = []
        for lang in langs:
            entry = by_lang.get(lang.code) or {}
            sample = ' '.join(
                str(entry.get(n) or '') for n in names if entry.get(n)
            ).strip()
            if sample and not self._looks_like_language(sample, lang.code):
                bad_langs.append(lang)

        if bad_langs:
            bad_codes = ', '.join(l.code for l in bad_langs)
            _logger.warning(
                'AI fill_seo multi-lang: response for %s had wrong-script '
                'values for [%s] — one consolidated retry.', record._name, bad_codes)
            stricter_ctx = FILL_CONTEXT + (
                '\n\nCRITICAL OVERRIDE: the previous response had wrong-language '
                'values for: {names}. Rewrite the WHOLE JSON; every language '
                'entry must be in its own script.'.format(names=bad_codes)
            )
            retry_response = agent.get_direct_response(
                prompt=prompt, context_message=stricter_ctx)
            retry_raw = retry_response[0] if retry_response else ''
            try:
                retry_parsed = self._parse_json(retry_raw)
                retry_by_lang = self._extract_multilang_fields(
                    retry_parsed, langs, names)
            except (ValueError, json.JSONDecodeError):
                retry_by_lang = {}
            for lang in bad_langs:
                entry = retry_by_lang.get(lang.code) or {}
                retry_sample = ' '.join(
                    str(entry.get(n) or '') for n in names if entry.get(n)
                ).strip()
                if retry_sample and self._looks_like_language(retry_sample, lang.code):
                    by_lang[lang.code] = entry

        try:
            confidence = float(parsed.get('confidence', 0.0))
        except (TypeError, ValueError):
            confidence = 0.0

        return {
            'by_lang': by_lang,
            'explanation': parsed.get('explanation', '') or '',
            'confidence': confidence,
            'model': agent.llm_model or _('AI agent'),
            'raw_json': raw,
        }

    @classmethod
    def _build_fill_prompt_multilang(cls, record, overwrite, langs, field_specs):
        """Build the consolidated multi-language fill prompt.

        Same shape as `_build_fill_prompt` but lists every language in
        the body and asks the model to return per-language field values
        nested under a ``by_lang`` key.
        """
        excerpt, h1, detected = cls._extract_page_signal(record)
        url = getattr(record, 'url', None) or record._get_seo_path() or '/'
        current_lines, produce_lines = [], []
        for spec in field_specs:
            name = spec['name']
            cur = getattr(record, name, None) or ''
            current_lines.append('  current_{n}: {v}'.format(
                n=name, v=json.dumps(cur) if cur else 'null'))
            produce_lines.append('  - {n}: {rule}'.format(
                n=name, rule=spec.get('rule', '')))
        lang_lines = ['    - "{code}" — write in {name}'.format(
            code=lang.code, name=(lang.name or lang.code)) for lang in langs]
        per_lang_shape = ', '.join(
            '"{}": "<value>"'.format(s['name']) for s in field_specs)
        by_lang_shape = ', '.join(
            '"{}": {{{shape}}}'.format(lang.code, shape=per_lang_shape)
            for lang in langs)
        header = (
            '### LANGUAGE-FIRST ###\n'
            'You will produce field values for EACH language listed below. '
            'EACH value MUST be in its OWN language script. Do not echo the '
            'page content language for languages that differ from it.\n\n'
        )
        return (
            '{header}'
            'INPUT:\n'
            '  mode: {mode}\n'
            '  url: {url}\n'
            '  page_h1: "{h1}"\n'
            '{current}\n'
            '  page_excerpt: "{excerpt}"\n'
            '  target_languages:\n'
            '{lang_list}\n'
            'FIELDS TO PRODUCE per language:\n'
            '{produce}\n'
            'FINAL CHECK before you answer:\n'
            '  - Is each field value written in its own language script?\n'
            '  - Do NOT translate the JSON keys; only the values.\n\n'
            'OUTPUT (one JSON object, no markdown):\n'
            '  {{"by_lang": {{{by_lang_shape}}}, '
            '"explanation": "<one sentence>", '
            '"confidence": <0.0-1.0>}}'.format(
                header=header,
                mode='rewrite all' if overwrite else 'fill missing',
                url=url,
                h1=h1.replace('"', "'"),
                current='\n'.join(current_lines),
                excerpt=excerpt.replace('"', "'"),
                lang_list='\n'.join(lang_lines),
                produce='\n'.join(produce_lines),
                by_lang_shape=by_lang_shape,
            )
        )

    @staticmethod
    def _extract_multilang_fields(parsed, langs, field_names):
        """Pull per-language field dicts from a multi-lang fill response.

        Strict contract: ``{"by_lang": {lang_code: {field: value, ...}}}``.
        Tolerant fallback: flat top-level ``{lang_code: {field: ...}}``.
        Returns {} if neither shape yields any populated language.
        """
        if not isinstance(parsed, dict):
            return {}
        sources = []
        if isinstance(parsed.get('by_lang'), dict):
            sources.append(parsed['by_lang'])
        sources.append(parsed)
        out = {}
        for src in sources:
            for lang in langs:
                entry = src.get(lang.code)
                if isinstance(entry, dict):
                    cleaned = {}
                    for fname in field_names:
                        v = entry.get(fname)
                        if v and isinstance(v, str) and v.strip():
                            cleaned[fname] = v.strip()
                    if cleaned and lang.code not in out:
                        out[lang.code] = cleaned
            if out:
                break
        return out

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

    def geo_content_review(self, record):
        """Review one page's content for GEO / AI-citability.

        Asks the agent to judge the page on the fixed GEO_REVIEW_CODES
        dimensions and returns a list of issue dicts for the dimensions that
        are genuinely a problem::

            [{'code', 'severity', 'title', 'detail', 'recommendation', 'model'}]

        Unknown codes and entries missing a title/recommendation are dropped,
        so the caller can trust every returned code is a known dimension.

        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        agent = self._resolve_agent()
        prompt = self._build_geo_review_prompt(record)
        response = agent.get_direct_response(
            prompt=prompt, context_message=GEO_REVIEW_CONTEXT)
        parsed = self._parse_json(response[0] if response else '')
        model = agent.llm_model or _('AI agent')
        issues = []
        for item in (parsed.get('issues') or []):
            if not isinstance(item, dict):
                continue
            code = (item.get('code') or '').strip()
            title = (item.get('title') or '').strip()
            recommendation = (item.get('recommendation') or '').strip()
            if code not in GEO_REVIEW_CODES or not title or not recommendation:
                continue
            severity = item.get('severity')
            if severity not in ('info', 'warning'):
                severity = 'info'
            issues.append({
                'code': code,
                'severity': severity,
                'title': title,
                'detail': (item.get('detail') or '').strip(),
                'recommendation': recommendation,
                'model': model,
            })
        return issues

    @classmethod
    def _build_geo_review_prompt(cls, record):
        excerpt, h1, detected = cls._extract_page_signal(record)
        url = getattr(record, 'url', None) or '/'
        return (
            'INPUT:\n'
            '  url: {url}\n'
            '  page_title_or_h1: "{h1}"\n'
            '  language_hint: {detected}\n'
            '  page_excerpt (first ~1500 chars): "{excerpt}"\n'
            'Judge the dimensions described in the system instructions and '
            'return the JSON object.'.format(
                url=url,
                h1=(h1 or '').replace('"', "'"),
                detected=detected,
                excerpt=(excerpt or '').replace('"', "'"),
            )
        )

    def propose_article(self, business_context, past_titles, existing_categories,
                        lang_code=None, trending_now=None, prompt_addendum=None,
                        related_pages=None, search_opportunities=None,
                        recent_subjects=None, focus_category=None,
                        progress=None, msg_writing=None, msg_extend=None):
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
        :param related_pages: iterable of ``{'title': str, 'url': str}``
                              internal targets the article may link to.
        :param search_opportunities: iterable of Search Console query dicts
                                     with query/impressions/clicks/position.
        :returns: dict ``{'title', 'subtitle', 'content_html', 'seo_title',
                          'seo_description', 'seo_keywords', 'category',
                          'image_prompt', 'trend_signal', 'reason',
                          'confidence'}``.
        :raises AIUnavailable / ValueError
        """
        ok, reason = self.is_available()
        if not ok:
            raise AIUnavailable(reason)
        import time as _time
        deadline = _time.monotonic() + self._ARTICLE_GEN_BUDGET_S
        agent = self._resolve_agent()
        prompt = self._build_article_prompt(
            business_context, past_titles, existing_categories,
            lang_code, trending_now, prompt_addendum, related_pages,
            search_opportunities, recent_subjects=recent_subjects,
            focus_category=focus_category)
        # The agent (a custom LLM here) intermittently returns a TRUNCATED reply
        # ("not valid JSON") or NOTHING at all ("Processing loop ended with no
        # response"). Those are fast failures, so retry a few times instead of
        # abandoning the whole run on the first bad reply — empirically ~4 of 7
        # succeed, so 3 tries make it reliable. A refusal still falls back to a
        # neutral prompt; every retry is gated by the wall-clock budget.
        parsed = None
        last_err = ''
        active_prompt = prompt
        for gen_attempt in range(1, 4):
            if progress and msg_writing:
                # Attempt 1 shows the plain step; only a RETRY gets a counter.
                # That way the normal path never shows a puzzling "(1)", and a
                # changing number ("2/3", "3/3") genuinely signals a retry — the
                # number no longer looks "stuck at 1" across separate runs.
                progress(msg_writing if gen_attempt == 1
                         else '%s (%d/3)' % (msg_writing, gen_attempt))
            try:
                response = agent.get_direct_response(
                    prompt=active_prompt, context_message=ARTICLE_CONTEXT)
                raw = (response[0] if response else '') or ''
                if self._looks_like_refusal(raw):
                    _logger.info(
                        'propose_article: agent refused; retrying with a neutral '
                        'prompt. First response: %r', raw[:200])
                    active_prompt = self._build_article_prompt(
                        business_context, past_titles, existing_categories,
                        lang_code, trending_now=None, prompt_addendum=None,
                        related_pages=related_pages,
                        search_opportunities=search_opportunities,
                        recent_subjects=recent_subjects,
                        focus_category=focus_category)
                    response = agent.get_direct_response(
                        prompt=active_prompt, context_message=ARTICLE_CONTEXT)
                    raw = (response[0] if response else '') or ''
                candidate = self._parse_json(raw)
                if ((candidate.get('title') or '').strip()
                        and (candidate.get('content_html') or '').strip()):
                    parsed = candidate
                    break
                last_err = 'reply missing title/content_html'
            except Exception as exc:  # noqa: BLE001 — flaky provider/agent reply
                last_err = str(exc) or exc.__class__.__name__
            _logger.warning(
                'propose_article: generation attempt %d/3 produced no usable '
                'article (%s)', gen_attempt, last_err[:160])
            if _time.monotonic() > deadline:
                break
        if parsed is None:
            raise ValueError(_(
                'AI returned no usable article after 3 attempts (the model — '
                '%s — kept replying empty or truncated). Last error: %s',
                getattr(agent, 'llm_model', '') or 'AI agent', last_err[:200]))
        # Word-count enforcement: quality floor.
        # Up to two extension passes — each takes the current best draft
        # and asks the agent to EXTEND it (not rewrite). Extending is
        # easier on the model than "rewrite longer" and avoids losing
        # good content from earlier passes. We keep the longest draft
        # we've seen.
        wc = self._count_words(parsed.get('content_html', ''))
        for attempt in range(1, 3):
            if wc >= self._ARTICLE_MIN_WORDS:
                break
            if _time.monotonic() > deadline:
                _logger.info(
                    'propose_article: %ds budget spent; publishing the %d-word '
                    'draft rather than risk a force-kill on more passes.',
                    self._ARTICLE_GEN_BUDGET_S, wc)
                break
            _logger.info(
                'propose_article: draft is %d words (< %d); extension '
                'pass %d/2', wc, self._ARTICLE_MIN_WORDS, attempt)
            if progress and msg_extend:
                # First pass = plain label; only a 2nd pass adds a counter, so
                # the banner never shows a puzzling "(1)" (same rule as writing).
                progress(msg_extend if attempt == 1
                         else '%s (%d/2)' % (msg_extend, attempt))
            extend_prompt = (
                'Below is the current draft article. It is %d words; the '
                'editorial floor is %d words (target %s). Return '
                'the SAME JSON shape, but with `content_html` EXTENDED to '
                'meet the floor by ADDING substantive material: search-intent '
                'coverage, concrete examples, decision criteria, named tools '
                'only when factual, specific Saudi-market context if relevant, '
                'an FAQ block, and practical next steps. Do not delete or '
                'paraphrase existing content; preserve title, trend_signal, '
                'category, image_prompt, and seo meta.\n\n'
                'CURRENT DRAFT (as JSON):\n%s'
            ) % (
                wc,
                self._ARTICLE_MIN_WORDS,
                self._ARTICLE_TARGET_WORDS,
                json.dumps(parsed, ensure_ascii=False),
            )
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
        if wc < self._ARTICLE_MIN_WORDS:
            _logger.warning(
                'propose_article: final draft only %d words (floor is %d) '
                'after retries; publishing anyway — review and extend '
                'manually if needed', wc, self._ARTICLE_MIN_WORDS)
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
                              lang_code=None, trending_now=None, prompt_addendum=None,
                              related_pages=None, search_opportunities=None,
                              recent_subjects=None, focus_category=None):
        # Step 5 enforcement: resolve a human language NAME and demand the whole
        # article be written in it. A bare locale code ("ar_001") wasn't forcing
        # the model hard enough, so Arabic sites got English drafts.
        _LANG_NAMES = {'ar': 'Arabic', 'en': 'English', 'fr': 'French',
                       'es': 'Spanish', 'de': 'German', 'tr': 'Turkish',
                       'ur': 'Urdu', 'hi': 'Hindi', 'id': 'Indonesian'}
        lang_prefix = (lang_code or '')[:2].lower()
        lang_name = _LANG_NAMES.get(lang_prefix, lang_code or 'the website language')
        lang_directive = (
            '### LANGUAGE — HARD REQUIREMENT ###\n'
            'Write the ENTIRE article in {ln}: title, subtitle, excerpt, every '
            'heading, paragraph, list item and FAQ, AND all SEO meta '
            '(seo_title, seo_description, seo_keywords). The inputs below may be '
            'in another language — compose/translate in {ln} regardless. Any '
            'output not written in {ln} is rejected.\n\n'.format(ln=lang_name)
            if lang_code else ''
        )
        focus_block = (
            '  assigned_category (MANDATORY — the generator rotates this to '
            'force topic spread): "{fc}"\n'.format(
                fc=str(focus_category).replace('"', "'"))
            if focus_category else ''
        )
        recent_items = []
        for item in list(recent_subjects or [])[:5]:
            if not isinstance(item, dict):
                continue
            title = str(item.get('title') or '').strip()
            if not title:
                continue
            recent_items.append({'title': title[:140],
                                 'category': str(item.get('category') or '').strip()[:80]})
        recent_block = (
            '  recently_covered (the LAST 5 articles with their subject + '
            'category — these fields are OFF-LIMITS for this one): {s}\n'
            .format(s=json.dumps(recent_items, ensure_ascii=False))
            if recent_items else ''
        )
        trends_block = (
            '  trending_now (Google Trends, daily, for the configured geo): {t}\n'
            .format(t=json.dumps(list(trending_now)[:15], ensure_ascii=False))
            if trending_now else ''
        )
        link_targets = []
        for item in list(related_pages or [])[:cls._ARTICLE_MAX_LINK_TARGETS]:
            if not isinstance(item, dict):
                continue
            title = str((item or {}).get('title') or '').strip()
            url = str((item or {}).get('url') or '').strip()
            if title and url:
                link_targets.append({'title': title[:120], 'url': url[:300]})
        links_block = (
            '  internal_link_targets (use only when genuinely relevant): {links}\n'
            .format(links=json.dumps(link_targets, ensure_ascii=False))
            if link_targets else ''
        )
        opportunity_items = []
        for item in list(search_opportunities or [])[:12]:
            if not isinstance(item, dict):
                continue
            query = str(item.get('query') or '').strip()
            if not query:
                continue
            opportunity_items.append({
                'query': query[:120],
                'impressions_28d': int(item.get('impressions') or 0),
                'clicks_28d': int(item.get('clicks') or 0),
                'avg_position': float(item.get('position') or 0.0),
            })
        opportunities_block = (
            '  search_opportunities (GSC, last 28 days): {items}\n'
            .format(items=json.dumps(opportunity_items, ensure_ascii=False))
            if opportunity_items else ''
        )
        addendum_block = (
            'ADMIN GUIDANCE (highest-priority, overrides anything else when in conflict):\n'
            '{a}\n\n'.format(a=str(prompt_addendum).strip())
            if prompt_addendum else ''
        )
        return (
            '{addendum}'
            '{lang_directive}'
            'INPUT:\n'
            '  business_name: "{name}"\n'
            '  business_summary: "{summary}"\n'
            '  target_language: {lang}\n'
            '  recent_post_titles: {past}\n'
            '  existing_categories: {cats}\n'
            '{trends}'
            '{links}'
            '{opportunities}'
            '{recent}'
            '{focus}'
            'TASK:\n'
            '  0z. ASSIGNED CATEGORY — if assigned_category is present it is a '
            'HARD directive that OVERRIDES your own topic choice: the article '
            'MUST be squarely about that category, and the output `category` '
            'MUST equal it verbatim. The host rotates this value every run to '
            'spread coverage across the whole site, so never substitute a '
            'different (e.g. the recently-dominant) field. Still honour the '
            'diversity and quality rules below within that category.\n'
            '  0y. NO FIELD DRIFT — the recent posts over-fixated on ONE field '
            '(the dominant field you named in step 0, whatever it is). Write '
            'about assigned_category strictly ON ITS OWN TERMS, for its OWN '
            'audience and sector. Do NOT narrow, reframe, or funnel an '
            'unrelated category back into that over-covered field; draw the '
            'examples, audiences and angles from the assigned domain itself, '
            'not from the dominant recent one. If a draft keeps pulling the '
            'topic toward the over-covered field, REWRITE it around the '
            'assigned domain real audience.\n'
            '  0. TOPIC DIVERSITY — decide this FIRST and treat it as a HARD, '
            'NON-NEGOTIABLE constraint. `recently_covered` lists the LAST 5 '
            'articles with their subject and category. You are FORBIDDEN from '
            'writing about any of those subjects, their categories, or a '
            'closely related field — even a different angle on the same field '
            'is rejected. Steps: (a) state in one phrase the dominant FIELD the '
            'recent_covered/recent_post_titles share (e.g. "factories / '
            'manufacturing"); (b) from internal_link_targets — the menu of '
            'services this business actually offers — choose a DIFFERENT '
            'service/field that is NOT that dominant field and NOT in '
            'recently_covered; (c) write about that. If the most obvious trend '
            'points back to the forbidden field, discard it and pick a trend '
            'relevant to the different field instead. Put the dominant-recent '
            'field you avoided AND the new field you chose in `reason`.\n'
            '  0a. business_summary describes the company identity and '
            'credentials ONLY — it is NOT a topic brief. It may over-emphasise a '
            'single programme or accreditation (e.g. a specific government '
            'grant); do NOT default the article to whatever the summary stresses. '
            'The real list of topics to rotate across is internal_link_targets + '
            'existing_categories, which together span the FULL range of services '
            'this business offers — treat every one of them as fair game and '
            'spread coverage evenly across them over time, not only the headline '
            'programme named in the summary.\n'
            '  1. TREND WITHIN THE CHOSEN DOMAIN. If assigned_category is set, '
            'the trend you pick MUST sit inside that domain — ignore trends that '
            'belong to a different field. If trending_now has an item genuinely '
            'relevant to this business AND the chosen domain, use it as your '
            'trend signal. Otherwise '
            'use search_opportunities to spot a real audience question or '
            'identify another current or emerging trend in that domain. Be '
            'specific (a concrete tool, behaviour, event, or shift), not '
            'generic ("AI is changing everything"). Surface the chosen signal '
            'in `trend_signal`.\n'
            '  1a. Define the primary reader intent before writing: what the '
            'reader came to learn, compare, decide, or do. Answer that intent '
            'clearly in the first 120 words, then go deeper.\n'
            '  1b. If search_opportunities are provided, treat them as demand '
            'signals, not a keyword-stuffing brief. Cover the relevant query '
            'intent naturally, including likely beginner and advanced wording, '
            'but only when the topic genuinely fits the business.\n'
            '  2. Write a complete article on that topic that the business '
            'could publish today. Substantive — AT LEAST {min_words} words '
            'of HTML, target {target_words} words. Count words in the body '
            'text only.\n'
            '  2a. STRUCTURE — `content_html` MUST contain, in order: an intro '
            'paragraph (3-5 sentences) that gives the useful answer quickly; '
            'FIVE to SEVEN named sections each headed with <h2> and each '
            'containing at least TWO substantive paragraphs (no single-sentence '
            'sections); at least one practical checklist, comparison, or '
            'step-by-step section using <ul>/<li>; a 3-5 question FAQ section '
            'with concise answers; and a short closing paragraph with a natural '
            'next step. This structure is what the word floor depends on — '
            'skipping sections will undershoot.\n'
            '  2b. PEOPLE-FIRST QUALITY — include business-specific insight, '
            'clear tradeoffs, examples, and caveats. Do not claim firsthand '
            'experience, prices, dates, laws, rankings, statistics, or product '
            'capabilities unless they are in the input. If the topic touches '
            'regulation or compliance, write cautiously and tell readers what '
            'to verify.\n'
            '  2c. LINKS — if internal_link_targets are provided, include 2-4 '
            'natural internal <a href="..."> links to the most relevant targets. '
            'Use descriptive anchor text. Do not invent URLs, do not force an '
            'irrelevant link, and never link every section.\n'
            '  3. Fill SEO meta and an image_prompt that an image-generation '
            'model could use as-is.\n'
            '  3a. SEO META — `seo_title` must be unique, clear, concise, and '
            'accurately describe this article. `seo_description` must be a '
            'short, unique, human-readable summary of the article with the '
            'most relevant point early; no clickbait, keyword lists, or claims '
            'the article does not support.\n'
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
                lang_directive=lang_directive,
                name=str(business_context.get('org_name') or '').replace('"', "'"),
                summary=str(business_context.get('summary') or '').replace('"', "'")[:600],
                lang=lang_name if lang_code else 'auto-detect from business_summary',
                past=json.dumps(list(past_titles)[:8], ensure_ascii=False),
                cats=json.dumps(list(existing_categories)[:30], ensure_ascii=False),
                trends=trends_block,
                links=links_block,
                opportunities=opportunities_block,
                recent=recent_block,
                focus=focus_block,
                min_words=cls._ARTICLE_MIN_WORDS,
                target_words=cls._ARTICLE_TARGET_WORDS,
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
        """Parse a JSON object from the model output, tolerating code fences
        and trailing prose / multiple objects.

        Cheap models routinely emit one valid JSON object followed by a
        prose explanation, a second object, or a stray trailing comma —
        plain ``json.loads`` chokes with ``Extra data``. We use
        ``json.JSONDecoder().raw_decode()`` to read ONE complete object
        from the start of the string and silently drop whatever follows.
        """
        text = (raw or '').strip()
        # Strip ```json ... ``` fences if the model added them despite instructions.
        fence = re.match(r'^```(?:json)?\s*(.*?)\s*```$', text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        # Narrow to the first brace-balanced object: this skips leading prose
        # AND drops a trailing chat-template token (</assistant>, <|im_end|>,
        # </s>, ...) since the scan stops at the matching '}'. It honors
        # strings, so a brace inside an Arabic value can't close early. We do
        # NOT strip control tokens here — that happens only in Layer B, so a
        # VALID response whose values legitimately contain <s>/<system>/etc.
        # is never mutated.
        text = _extract_json_object_span(text)
        if not text:
            raise ValueError(
                'AI returned empty output where JSON was expected.')
        # Layer A — strict, lossless. raw_decode reads ONE object and returns
        # (parsed, end_index); anything past end_index (prose, a second object)
        # is silently dropped — the common "JSON + then a paragraph" shape.
        try:
            parsed, _end = json.JSONDecoder().raw_decode(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        # Layer B — tolerant repair (strip stray control tokens, trailing
        # commas, an unquoted string value), then re-validate with the SAME
        # strict decoder so a bad repair fails closed rather than returning
        # wrong data. A non-object result (e.g. a bare list) is also rejected.
        try:
            parsed, _end = json.JSONDecoder().raw_decode(_repair_json_text(text))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # All layers failed (or produced a non-object). No _() wrapper here:
        # this helper is a @staticmethod called from many paths including
        # crons, where the call frame has no env/uid for Odoo's translation
        # system to walk to. Wrapping forces _get_lang() to log a "no
        # translation language detected" WARNING with a multi-line stack trace
        # per refused JSON. The message reaches users (if at all) via the
        # cron's WARNING line and not via UI, so plain English is fine.
        raise ValueError(
            'AI returned output that is not valid JSON: %s' % (raw or '')[:200])
        # NOTE: the legacy suggest_fix contract requires `proposed_value`,
        # but every other caller uses a different JSON shape (propose_article:
        # title/content_html; pick_schema: code; pick_blog_taxonomy: category;
        # fill_seo: per-field names). Callers that need `proposed_value`
        # validate it themselves — see `_field_and_mechanical_fix`. Each layer
        # above returns on success, so there is no fall-through return here.

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

    @classmethod
    def _loads_json(cls, raw):
        """Parse a JSON object from model output. Delegates to the hardened
        ``_parse_json`` so the schema / image-alt / html / choice / fill family
        inherits control-token stripping, brace-balanced extraction, and the
        tolerant repair pass. (Replaces a weaker engine that used plain
        ``json.loads`` plus a greedy ``\\{.*\\}`` span — which could over-grab
        to the last ``}`` across a trailing object/prose.)"""
        return cls._parse_json(raw)

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
    def _build_thin_prompt(cls, target, target_language=None):
        excerpt, h1, detected = cls._extract_page_signal(target)
        target_language = target_language or detected
        return (
            'INPUT:\n'
            '  page_topic: "{h1}"\n'
            '  target_language: {target_language}\n'
            '  detected_language: {detected}\n'
            '  current_content_excerpt: "{excerpt}"\n'
            'CRITICAL: write EVERY heading, paragraph and list item in '
            '{target_language} (the website language). Do NOT answer in '
            'English or any other language.\n'
            'OUTPUT:'.format(
                h1=h1.replace('"', "'"),
                target_language=target_language,
                detected=detected,
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

    def _rendered_missing_alt(self, record):
        """How many <img> lack alt on the RENDERED page — what the audit's
        image-alt check actually counts. Reuses the audit's page renderer (same
        HTTP fetch + #wrap scope + per-run cache); returns 0 when the page isn't
        reachable. Lets the fixer be honest about images it cannot edit from the
        page's own content (theme / dynamic-snippet images live outside the arch)."""
        try:
            Audit = self.env.get('era.seo.audit.run')
            doc = Audit._rendered_wrap_doc(record) if Audit is not None else None
            if doc is None:
                return 0
            return sum(1 for img in doc.xpath('//img')
                       if not (img.get('alt') or '').strip())
        except Exception:  # noqa: BLE001 — diagnostics only, never block the fix
            return 0

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

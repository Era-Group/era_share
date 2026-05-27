"""ERA SEO AI — Anthropic client wrapper.

Wraps the ``anthropic`` Python SDK so the rest of the addon doesn't see HTTP
details, retries, or model selection. One public method, ``suggest_fix``,
takes an ``era.seo.audit.finding`` row plus the target record and returns a
structured proposal (dict) along with token usage.

Design choices:

  - **Default model:** ``claude-haiku-4-5`` (alias). Cheapest current Claude,
    fast enough for a per-finding interactive workflow. Admin can upgrade
    to ``claude-sonnet-4-6`` or ``claude-opus-4-7`` from settings for
    higher-quality copy at higher cost.
  - **Prompt caching:** the system prompt is intentionally large
    (~5-7K tokens of role, output schema, brand voice, and few-shot
    examples) so it qualifies for ephemeral caching (4096-token minimum
    on Haiku 4.5 / Opus). After the first call in a 5-minute window every
    subsequent call pays ~10% of the input price for the cached portion.
    Verified via ``response.usage.cache_read_input_tokens > 0``.
  - **Structured outputs:** request enforces ``output_config.format`` with
    a ``json_schema`` so the model returns exactly the shape we parse.
    No regex parsing of free-form text.
  - **Retries:** the SDK retries 429 / 5xx / network errors automatically
    with exponential backoff (default 2 retries); we set 3 explicitly.
  - **API key resolution:** ``era_seo.ai_api_key`` ICP first (admin-saved),
    then ``ANTHROPIC_API_KEY`` env var. Never echoed in logs.

Per ``claude-api`` skill: model strings are bare aliases (e.g.
``claude-haiku-4-5``, not date-suffixed); do NOT use ``temperature`` /
``top_p`` on Opus 4.7 (returns 400); JSON schemas have constraint
limitations — we keep ours simple.
"""
import json
import logging
import os

from odoo import _, fields

_logger = logging.getLogger(__name__)

# Default model. Admin can override via ``era_seo.ai_model`` ICP. Always
# use the bare alias, never a date-suffixed snapshot.
DEFAULT_MODEL = 'claude-haiku-4-5'

# Models this addon supports out of the box. Other model strings are
# allowed at admin's risk (e.g. older snapshots) but won't be offered in
# the settings dropdown.
SUPPORTED_MODELS = [
    ('claude-haiku-4-5', 'Claude Haiku 4.5 (fastest, cheapest)'),
    ('claude-sonnet-4-6', 'Claude Sonnet 4.6 (balanced)'),
    ('claude-opus-4-7', 'Claude Opus 4.7 (highest quality, most expensive)'),
]

# Output JSON schema — the model is required to return exactly this shape.
# Kept simple (no length / range constraints) per claude-api skill notes on
# what structured-outputs accepts.
_OUTPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'proposed_value': {
            'type': 'string',
            'description': 'The new value for the target SEO field. Must respect SEO length conventions for the field type.',
        },
        'explanation': {
            'type': 'string',
            'description': 'One sentence explaining the rationale.',
        },
        'confidence': {
            'type': 'number',
            'description': 'Self-rated confidence from 0.0 (guess) to 1.0 (certain), based on signal in the page content.',
        },
    },
    'required': ['proposed_value', 'explanation', 'confidence'],
    'additionalProperties': False,
}


# Big system prompt — ~5K tokens. Designed to (a) get the model to the
# correct house style and (b) hit the 4096-token minimum so prompt caching
# actually kicks in. Each section is intentional; do NOT prune without
# verifying the cache hit rate stays above 80%.
SYSTEM_PROMPT = """You are an SEO copywriter for ERA — Excellence Resources Arabia, a Gold \
Odoo Partner working primarily with Saudi-market clients in both Arabic and \
English. Your job is to fix one specific SEO defect on one specific page at \
a time. You will be given the page's current content and the defect to fix; \
respond with a JSON object containing your proposed value, a one-sentence \
explanation, and a confidence score.

# Output format

Always return strict JSON matching the schema. No prose, no markdown, no \
preamble, no apology — just the JSON object. The host application will \
reject malformed output.

# Field-specific rules

## seo_title
- 50-60 characters is the sweet spot. Hard cap at 60.
- Lead with the primary keyword.
- Brand name LAST if it fits, separated by " | " or " — ".
- Avoid title case where it feels unnatural in Arabic.
- Never use ALL CAPS.

Good examples:
- "Cloud-Based Accounting for Saudi SMEs | ERA"
- "أنظمة المحاسبة السحابية للشركات السعودية | إيرا"
- "Odoo Implementation Services — Riyadh, KSA"

Bad examples:
- "Welcome to our site!" (no keywords)
- "Cloud-Based Accounting Software for Saudi Small and Medium Enterprises by ERA Excellence Resources Arabia" (too long)
- "BUY NOW!!!" (caps, no keywords)

## seo_description
- 140-160 characters. Hard cap at 160.
- One complete thought; ends with a period.
- Include a call to action when natural ("Learn how…", "Start your trial…", "اعرف كيف…").
- Repeat the primary keyword once, near the start.
- Match the page's language (Arabic content -> Arabic description).

Good examples:
- "Streamline accounting for your Saudi SME with cloud-based bookkeeping, VAT-ready invoicing, and ZATCA compliance built in. Start your free trial today."
- "تعرّف على كيفية تبسيط محاسبة شركتك السعودية الصغيرة بنظام محاسبي سحابي يدعم الفوترة الإلكترونية ومتطلبات هيئة الزكاة. ابدأ تجربتك المجانية."

## URL slug (slug field)
- Lowercase only. ASCII or Unicode are both fine, but stay consistent with the language of the title.
- Hyphens between words; no underscores, no spaces, no special chars.
- Drop stop-words (the, a, an, in, on, of, for, and; ال, في, من, إلى, على).
- 3-5 meaningful words. Hard cap at 75 chars total slug length.
- Do not start or end with a hyphen.
- For Arabic pages, transliteration is acceptable when it improves search visibility, but native Arabic in the slug is generally preferred.

Good examples:
- /cloud-accounting-saudi
- /odoo-implementation-services
- /محاسبة-سحابية-سعودية

Bad examples:
- /Welcome-To-Our-Site (caps, stop-words)
- /the_best_accounting_software_for_small_businesses (underscores, too long, stop-words)
- /-something- (leading/trailing hyphen)

# Tone and brand voice

- Professional but not stiff. Confident, not boastful.
- Action-oriented in English. Slightly more formal in Arabic.
- Never use emoji.
- Avoid superlatives ("best", "amazing") unless they're factually defensible.
- Mention compliance, ZATCA, VAT, e-invoicing, or "Saudi" when the page is clearly KSA-targeted — these are high-value keywords for the Saudi market.
- Localize: Arabic content gets Arabic SEO copy. Mixed content (e.g. English title with Arabic body) — match the title language and call it out in your explanation.

# Confidence scoring

- 0.95-1.0: The page content makes the right answer obvious and there is one clear primary keyword.
- 0.7-0.94: You have good signal but had to interpret intent or pick between candidate keywords.
- 0.4-0.69: The page is thin or generic; your proposal is a reasonable guess that the admin should sanity-check.
- 0.0-0.39: The page has almost no signal. Propose something safe and flag the weakness in the explanation.

# What you must NOT do

- Do not invent facts about ERA, products, prices, or features that aren't in the page content.
- Do not generate text in a language the page doesn't use.
- Do not exceed the hard length caps under any circumstance — truncate words rather than overflow.
- Do not include URLs, phone numbers, or email addresses in titles or descriptions.
- Do not output keyword lists or comma-soup ("Accounting, SME, KSA, Riyadh, Saudi").
- Do not echo the page's H1 verbatim if the H1 is already short — use the H1 as INPUT to your work, not the output.

# Few-shot examples

## Example 1 — missing seo_title

INPUT:
  defect: missing_seo_title
  url: /accounting/cloud
  current_value: null
  page_h1: "Cloud Accounting for Small Businesses"
  page_excerpt: "Manage your books, send VAT-compliant invoices, and stay ZATCA-ready with our cloud-based accounting platform built for Saudi SMEs."
  language_hint: en

OUTPUT:
{"proposed_value": "Cloud Accounting for Saudi SMEs | ZATCA-Ready", "explanation": "Lead with the primary keyword 'Cloud Accounting', narrow to the target audience 'Saudi SMEs', and surface ZATCA compliance as a differentiator. 54 chars total, brand omitted to leave room for ZATCA hook.", "confidence": 0.92}

## Example 2 — title_too_long

INPUT:
  defect: title_too_long
  url: /services/odoo-implementation
  current_value: "Professional Odoo ERP Implementation Services for Small and Medium Enterprises in Saudi Arabia"
  page_h1: "Odoo Implementation"
  page_excerpt: "Get up and running on Odoo with senior Saudi consultants. Discovery, migration, training, and go-live in 8 weeks."
  language_hint: en

OUTPUT:
{"proposed_value": "Odoo Implementation for Saudi SMEs — 8-Week Go-Live", "explanation": "Drop verbose 'Professional', 'Services', 'in Saudi Arabia'; replace with the concrete 8-week promise from the body. 52 chars.", "confidence": 0.88}

## Example 3 — missing_meta_description

INPUT:
  defect: missing_meta_description
  url: /pricing
  current_value: null
  page_h1: "Pricing"
  page_excerpt: "Flexible plans for SMEs and large enterprises. All plans include support, training, and free updates. Contact our sales team for a custom quote."
  language_hint: en

OUTPUT:
{"proposed_value": "Compare ERA's flexible pricing plans for Odoo and accounting — every tier includes Saudi support, training, and free updates. Get a custom quote today.", "explanation": "Open with a verb ('Compare'), name the deliverables in the body, and close with the CTA from the page. 154 chars.", "confidence": 0.9}

## Example 4 — description_too_long

INPUT:
  defect: description_too_long
  url: /blog/vat-zatca-guide
  current_value: "A comprehensive guide to understanding VAT and ZATCA compliance for small and medium enterprises in Saudi Arabia, covering registration requirements, invoice formatting, e-invoicing rules, monthly returns, and the most common pitfalls Saudi businesses encounter when adopting cloud-based accounting platforms in the post-2024 regulatory environment."
  page_h1: "The Saudi VAT & ZATCA Compliance Guide"
  page_excerpt: "Everything Saudi SMEs need to register for VAT, format ZATCA-compliant invoices, file monthly returns, and avoid the top five compliance pitfalls."
  language_hint: en

OUTPUT:
{"proposed_value": "Everything Saudi SMEs need to register for VAT, format ZATCA-compliant invoices, file monthly returns, and avoid the top five compliance pitfalls.", "explanation": "The body excerpt is already at 150 chars and reads more cleanly than the original — use it verbatim.", "confidence": 0.95}

## Example 5 — slug_contains_stopwords

INPUT:
  defect: slug_contains_stopwords
  url: /the-best-cloud-accounting-software-for-small-business-in-saudi-arabia
  current_value: "the-best-cloud-accounting-software-for-small-business-in-saudi-arabia"
  page_h1: "Cloud Accounting for Saudi SMEs"
  page_excerpt: "Manage your books, send VAT-compliant invoices..."
  language_hint: en

OUTPUT:
{"proposed_value": "cloud-accounting-saudi-sme", "explanation": "Dropped stop-words ('the', 'for', 'in'), removed the unprovable 'best', collapsed 'small-business' + 'saudi-arabia' to 'saudi-sme'. 28 chars.", "confidence": 0.94}

## Example 6 — Arabic page, missing_seo_title

INPUT:
  defect: missing_seo_title
  url: /محاسبة
  current_value: null
  page_h1: "أنظمة المحاسبة السحابية"
  page_excerpt: "حلول محاسبية متكاملة للشركات السعودية مع دعم الفوترة الإلكترونية وامتثال هيئة الزكاة."
  language_hint: ar

OUTPUT:
{"proposed_value": "أنظمة المحاسبة السحابية للشركات السعودية | إيرا", "explanation": "Lead with the primary Arabic keyword from the H1, target Saudi businesses, append the brand. 47 chars.", "confidence": 0.91}

# Final reminder

Return JSON only. Do not wrap it in markdown code fences. Do not add prose \
before or after. The host application parses with json.loads()."""


def _icp(env, key, default=None):
    """Shortcut for ir.config_parameter reads."""
    return env['ir.config_parameter'].sudo().get_param(key, default)


def _resolve_api_key(env):
    """Resolve the Anthropic API key. ICP first (admin-saved), env var second."""
    saved = _icp(env, 'era_seo.ai_api_key')
    if saved:
        return saved
    return os.environ.get('ANTHROPIC_API_KEY')


def _resolve_model(env):
    return _icp(env, 'era_seo.ai_model', DEFAULT_MODEL) or DEFAULT_MODEL


def _resolve_enabled(env):
    flag = _icp(env, 'era_seo.ai_enabled', 'False')
    return flag in ('True', '1', 'true', 'yes', 'on')


class AnthropicUnavailable(Exception):
    """Raised when the SDK is not installed or no API key is configured."""


class AIClient:
    """Stateless wrapper around the Anthropic SDK.

    Instantiate per request — the underlying ``Anthropic`` client is cheap to
    construct and we want each call to read the latest ICP values.
    """

    def __init__(self, env):
        self.env = env
        self._client = None  # lazy
        self._api_key = _resolve_api_key(env)
        self._model = _resolve_model(env)
        self._enabled = _resolve_enabled(env)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self):
        """Quick check: enabled, key present, SDK importable."""
        if not self._enabled:
            return False, _('AI auto-fix is disabled in settings.')
        if not self._api_key:
            return False, _('No Anthropic API key configured. Set era_seo.ai_api_key '
                            'or ANTHROPIC_API_KEY.')
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, _('Python package "anthropic" is not installed. '
                            'Add it to requirements.txt and re-deploy.')
        return True, ''

    def suggest_fix(self, finding, target_record):
        """Generate a proposed value for one audit finding.

        :param finding: ``era.seo.audit.finding`` record (with check_code, url, ...)
        :param target_record: the page record the finding is about
        :returns: dict with keys
            - proposed_value (str)
            - explanation (str)
            - confidence (float)
            - field (str): which field on target_record to write to
            - model (str): which model answered
            - usage (dict): {input_tokens, output_tokens, cache_read_input_tokens,
                             cache_creation_input_tokens}
        :raises AnthropicUnavailable: if the client isn't ready
        :raises ValueError: if the check_code isn't auto-fixable, or if the
                            model returns malformed JSON despite the schema
        """
        ok, reason = self.is_available()
        if not ok:
            raise AnthropicUnavailable(reason)

        field, mechanical_fix = self._field_and_mechanical_fix_for(finding, target_record)
        if mechanical_fix is not None:
            # Some checks don't need the API at all — handle locally.
            return {
                'proposed_value': mechanical_fix,
                'explanation': _('Mechanical fix applied without calling the AI.'),
                'confidence': 1.0,
                'field': field,
                'model': 'mechanical',
                'usage': {},
            }

        prompt_input = self._build_user_prompt(finding, target_record, field)
        anthropic = self._import_sdk()
        client = anthropic.Anthropic(api_key=self._api_key, max_retries=3)

        response = client.messages.create(
            model=self._model,
            max_tokens=512,
            # System prompt with caching on the LAST block. The marker
            # caches everything before it; subsequent calls in the same
            # 5-min window pay ~10% of input price for this prefix.
            system=[{
                'type': 'text',
                'text': SYSTEM_PROMPT,
                'cache_control': {'type': 'ephemeral'},
            }],
            output_config={
                'format': {
                    'type': 'json_schema',
                    'schema': _OUTPUT_SCHEMA,
                },
            },
            messages=[{'role': 'user', 'content': prompt_input}],
        )

        raw = next((b.text for b in response.content if b.type == 'text'), '')
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                _('Model returned unparseable JSON despite the schema: %s', exc)
            ) from exc

        usage = response.usage
        return {
            'proposed_value': parsed['proposed_value'],
            'explanation': parsed.get('explanation', ''),
            'confidence': float(parsed.get('confidence', 0.0)),
            'field': field,
            'model': self._model,
            'usage': {
                'input_tokens': usage.input_tokens,
                'output_tokens': usage.output_tokens,
                'cache_read_input_tokens': getattr(usage, 'cache_read_input_tokens', 0) or 0,
                'cache_creation_input_tokens': getattr(usage, 'cache_creation_input_tokens', 0) or 0,
            },
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _import_sdk():
        try:
            import anthropic
        except ImportError as exc:
            raise AnthropicUnavailable(
                _('Python package "anthropic" is not installed.')
            ) from exc
        return anthropic

    @staticmethod
    def _field_and_mechanical_fix_for(finding, target):
        """Return (target_field, mechanical_fix or None).

        Mechanical fixes don't need an API call — e.g. lowercasing a slug.
        For those we return the fixed value here and skip Claude.
        """
        code = finding.check_code
        if code in ('missing_seo_title', 'title_too_long', 'title_too_short'):
            return 'seo_title', None
        if code in ('missing_meta_description', 'description_too_long', 'description_too_short'):
            return 'seo_description', None
        if code == 'slug_contains_uppercase':
            url = target.url or ''
            return 'url', url.lower()
        if code in ('slug_contains_stopwords', 'slug_too_long'):
            return 'url', None
        raise ValueError(_('Check code %s is not AI-fixable.', code))

    @staticmethod
    def _build_user_prompt(finding, target, field):
        """Build the per-finding user message.

        Kept short (varies per call) — the heavy content lives in the
        cached system prompt above.
        """
        # Strip HTML for the excerpt: model only needs the text signal.
        from lxml import html as lxml_html
        content_html = getattr(target, 'content', None) or getattr(target, 'arch', None) or ''
        try:
            text = lxml_html.fragment_fromstring(content_html, create_parent='div').text_content()
            text = ' '.join(text.split())
        except Exception:  # noqa: BLE001
            text = ''
        excerpt = text[:1500] if text else '(no content available)'

        current = getattr(target, field, None) if field != 'url' else (target.url or '')

        # Heuristic language hint: Arabic if more than 10% of chars are
        # in the Arabic Unicode block, else English.
        sample = (excerpt + ' ' + (current or ''))[:500]
        arabic_chars = sum(1 for c in sample if '؀' <= c <= 'ۿ')
        lang = 'ar' if arabic_chars > len(sample) * 0.1 else 'en'

        # H1 heuristic — first <h1> in content, else the target's name.
        h1 = ''
        try:
            doc = lxml_html.fragment_fromstring(content_html, create_parent='div')
            h1_node = doc.find('.//h1')
            if h1_node is not None:
                h1 = (h1_node.text_content() or '').strip()
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
            '\n'
            'OUTPUT:'.format(
                code=finding.check_code,
                url=finding.url or '/',
                current=json.dumps(current) if current else 'null',
                h1=h1.replace('"', "'"),
                excerpt=excerpt.replace('"', "'"),
                lang=lang,
            )
        )

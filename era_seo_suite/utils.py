"""ERA SEO Blog — shared utilities."""
import hashlib
import re
import unicodedata

# Lightweight Arabic -> Latin transliteration so Arabic names yield
# readable, URL-safe slugs instead of collapsing to an empty string.
# This is a pragmatic, not scholarly, mapping aimed at producing stable
# and distinct slugs for Arabic content.
_AR_TRANSLIT = {
    'ا': 'a', 'أ': 'a', 'إ': 'i', 'آ': 'a', 'ء': '', 'ؤ': 'u', 'ئ': 'i',
    'ب': 'b', 'ت': 't', 'ث': 'th', 'ج': 'j', 'ح': 'h', 'خ': 'kh',
    'د': 'd', 'ذ': 'dh', 'ر': 'r', 'ز': 'z', 'س': 's', 'ش': 'sh',
    'ص': 's', 'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'gh',
    'ف': 'f', 'ق': 'q', 'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n',
    'ه': 'h', 'و': 'w', 'ي': 'y', 'ى': 'a', 'ة': 'h',
    'ً': '', 'ٌ': '', 'ٍ': '', 'َ': '', 'ُ': '', 'ِ': '', 'ّ': '', 'ْ': '',
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
}


def _transliterate(s):
    """Map known non-Latin characters to Latin equivalents."""
    return ''.join(_AR_TRANSLIT.get(ch, ch) for ch in s)


def slugify(s, fallback='item'):
    """Convert a string to a non-empty, URL-safe slug (lowercase, hyphens, ASCII).

    Replaces odoo.tools.slugify which was removed after Odoo 16. Unlike a naive
    ASCII strip, this transliterates Arabic and, as a last resort, derives a
    deterministic token from the original text so the result is *never* empty
    (an empty slug breaks the UNIQUE(slug) constraint on blog categories/series).
    """
    original = s or ''
    s = _transliterate(original)
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    s = s.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-{2,}', '-', s)
    s = s.strip('-')
    if not s:
        # Original had no transliterable/ASCII content (e.g. emoji-only).
        # Derive a stable token so distinct names get distinct slugs.
        stripped = original.strip()
        digest = hashlib.md5(stripped.encode('utf-8')).hexdigest()[:8]
        s = f'{fallback}-{digest}' if stripped else fallback
    return s


def unique_slug(model_sudo, base, exclude_id=None):
    """Return a slug derived from ``base`` that is unique for ``model_sudo``.

    Appends a numeric suffix (-2, -3, ...) on collision. Searches with
    ``active_test=False`` so archived records still reserve their slug.
    """
    if isinstance(base, dict):  # translated field value (e.g. {'en_US': '...'})
        base = next((v for v in base.values() if v), '')
    base = slugify(base)
    Model = model_sudo.with_context(active_test=False)
    candidate = base
    n = 2
    while True:
        domain = [('slug', '=', candidate)]
        if exclude_id:
            domain.append(('id', '!=', exclude_id))
        if not Model.search_count(domain):
            return candidate
        candidate = f'{base}-{n}'
        n += 1

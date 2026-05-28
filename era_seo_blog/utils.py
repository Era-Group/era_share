"""ERA SEO Blog — shared utilities."""
import re
import unicodedata


def slugify(s):
    """Convert a string to a URL-safe slug (lowercase, hyphens, ASCII).

    Replaces odoo.tools.slugify which was removed after Odoo 16.
    """
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    s = s.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_]+', '-', s)
    s = re.sub(r'-{2,}', '-', s)
    return s.strip('-')

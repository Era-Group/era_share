"""Render responses that were stored before the field became Html.

Answers written while the field was Text are plain markdown. Left as they are they
would show as raw text in a field that now expects markup -- headings as literal
hashes, no paragraphs. They are converted the same way a new answer is.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT id, sanitized_response FROM legal_ai_request
        WHERE sanitized_response IS NOT NULL
          AND sanitized_response <> ''
          AND sanitized_response !~ '<[a-zA-Z][^>]*>'
    """)
    rows = cr.fetchall()
    if not rows:
        return

    try:
        from markdown2 import markdown
    except ImportError:
        markdown = None
    from odoo.tools import html_sanitize
    from odoo.tools.mail import plaintext2html

    for request_id, text in rows:
        try:
            rendered = html_sanitize(markdown(
                text, extras=['fenced-code-blocks', 'tables', 'strike'])) if markdown else plaintext2html(text)
        except Exception:
            rendered = plaintext2html(text)
        cr.execute("UPDATE legal_ai_request SET sanitized_response = %s WHERE id = %s",
                   (rendered, request_id))

    _logger.info('era_law_firm_ai: rendered %s stored response(s) that predate the Html field', len(rows))

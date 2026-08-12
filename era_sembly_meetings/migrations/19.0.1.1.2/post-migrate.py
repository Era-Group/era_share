# -*- coding: utf-8 -*-
"""Un-escape summaries that were stored before Sembly's HTML was recognised.

Until 19.0.1.1.2 the importer ran ``plaintext2html`` over ``minutes[].text``,
which escapes every ``<``. Sembly actually sends markup, so those meetings show
the tags — ``<p><h2><b>✨ Summary</b></h2></p>`` — instead of the summary.

Re-importing would also fix them, but only for whoever knows to do it, so the
repair happens here. It is:

* **targeted** — only rows whose summary contains an escaped KNOWN tag, so a
  legitimately escaped ``revenue &lt; 100k`` is never touched on its own;
* **idempotent** — after the repair the row no longer matches the pattern;
* **safe** — the result goes back through ``html_sanitize``, exactly as a fresh
  import would.
"""
import html
import logging
import re

from odoo import api, SUPERUSER_ID
from odoo.tools import html_sanitize

_logger = logging.getLogger(__name__)

# An ESCAPED known tag: '&lt;p&gt;', '&lt;/h2&gt;', '&lt;b&gt;', …
ESCAPED_TAG_RE = re.compile(
    r'&lt;/?(?:p|br|hr|b|i|u|em|strong|h[1-6]|ul|ol|li|div|span|a|table|tr|td|'
    r'th|blockquote|pre|code)\b[^&]*&gt;', re.IGNORECASE)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT id, summary FROM sembly_meeting
         WHERE summary IS NOT NULL AND summary <> ''
    """)
    rows = [(rid, body) for rid, body in cr.fetchall()
            if body and ESCAPED_TAG_RE.search(body)]
    if not rows:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    for rid, body in rows:
        repaired = html_sanitize(html.unescape(body))
        # sembly_sync so the write cannot trip the manual-link guard, and sudo
        # so it cannot trip the content lock.
        env['sembly.meeting'].browse(rid).sudo().with_context(
            sembly_sync=True).write({'summary': repaired})
    _logger.info("Sembly: un-escaped %s summary/summaries stored before the "
                 "HTML fix", len(rows))

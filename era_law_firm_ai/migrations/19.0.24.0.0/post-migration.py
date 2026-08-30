"""Send citation links back to the indexed copy.

19.0.23.0.0 pointed them at laws.moj.gov.sa, on the reasoning that the
ministry is the authority. In practice those deep links do not reliably
resolve — a citation that leads nowhere is worse than one leading to the copy
we hold, which at least shows the lawyer the text the answer was drawn from.

Clearing source.url is enough: core falls back to /web/content/<attachment>
on its own. The URLs remain on moj.law, so nothing is lost if the ministry's
addressing becomes dependable later.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sources = env["ai.agent.source"].search([
        ("type", "=", "binary"),
        ("url", "!=", False),
    ])
    if sources:
        sources.write({"url": False})
    _logger.info("MoJ corpus: citation links returned to the indexed copy "
                 "for %s source(s)", len(sources))

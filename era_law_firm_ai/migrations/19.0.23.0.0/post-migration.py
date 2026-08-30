"""Carry the official MoJ URL onto statute sources already in the database.

Before this, every citation in a legal answer linked to the copy indexed
inside Odoo instead of the article on laws.moj.gov.sa. The URLs were already
stored on moj.law; they had simply never been passed on to the sources, which
is where core reads them from when it builds a citation link.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    laws = env["moj.law"].search([("source_url", "!=", False)])
    touched = laws._apply_official_url_to_sources()
    _logger.info(
        "MoJ corpus: official URL applied to %s source(s) across %s statute(s)",
        len(touched), len(laws))

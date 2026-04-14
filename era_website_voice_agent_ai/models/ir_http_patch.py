import logging

from odoo import models
from odoo.http import request

_logger = logging.getLogger(__name__)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _serve_redirect(cls):
        # REQUEST_URI may be absent for non-standard WSGI callers (e.g. websocket path probes).
        # Fall back to the request path so the redirect lookup still works.
        request.httprequest.environ.setdefault(
            'REQUEST_URI', request.httprequest.full_path.rstrip('?') or request.httprequest.path
        )
        return super()._serve_redirect()

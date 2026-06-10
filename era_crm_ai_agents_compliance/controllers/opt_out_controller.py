# -*- coding: utf-8 -*-
"""Public opt-out (unsubscribe) endpoint.

A recipient opting out is, by definition, NOT logged in, so this controller is
``auth='public'`` and uses a single, narrowly-scoped sudo elevation (the
approved opt-out elevation): validate a signed token → resolve the partner →
call ``process_opt_out``. It writes nothing else under sudo.

A GET shows a confirmation page; the actual withdrawal happens on POST, so that
link-prefetchers / email scanners cannot trigger an opt-out by merely following
the URL. The signed token authenticates the request, so CSRF is disabled on the
POST (there is no session to forge).
"""
from odoo import http
from odoo.http import request

from ..services import opt_out

_ROUTE = "/crm_ai/opt_out/<int:partner_id>/<string:token>"


class CrmAiOptOutController(http.Controller):

    @http.route(_ROUTE, type="http", auth="public", methods=["GET"], csrf=False)
    def opt_out_form(self, partner_id, token, **kw):
        if not opt_out.verify_token(request.env, partner_id, token):
            return self._page(_INVALID, status=403)
        return self._page(_CONFIRM_FORM % {"action": request.httprequest.path})

    @http.route(_ROUTE, type="http", auth="public", methods=["POST"], csrf=False)
    def opt_out_confirm(self, partner_id, token, **kw):
        if not opt_out.verify_token(request.env, partner_id, token):
            return self._page(_INVALID, status=403)
        # Approved narrow sudo: the recipient is not authenticated.
        opt_out.process_opt_out(request.env.sudo(), int(partner_id), source="public-link")
        return self._page(_DONE)

    @staticmethod
    def _page(body, status=200):
        html = _LAYOUT % {"body": body}
        return request.make_response(
            html,
            headers=[("Content-Type", "text/html; charset=utf-8")],
            status=status,
        )


_LAYOUT = (
    "<!DOCTYPE html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>إلغاء الاشتراك</title></head>"
    "<body style='font-family:sans-serif;max-width:560px;margin:40px auto;"
    "text-align:center;color:#222'>%(body)s</body></html>"
)
_CONFIRM_FORM = (
    "<h2>إلغاء الاشتراك من الرسائل التسويقية</h2>"
    "<p>هل ترغب بإيقاف استقبال الرسائل التسويقية؟<br>"
    "Do you want to stop receiving marketing messages?</p>"
    "<form method='post' action='%(action)s'>"
    "<button type='submit' style='padding:10px 24px;font-size:16px;cursor:pointer'>"
    "نعم، ألغِ الاشتراك / Yes, unsubscribe</button></form>"
)
_DONE = (
    "<h2>تم إلغاء الاشتراك</h2>"
    "<p>لن تصلك رسائل تسويقية بعد الآن.<br>"
    "You have been unsubscribed from marketing messages.</p>"
)
_INVALID = (
    "<h2>رابط غير صالح / Invalid link</h2>"
    "<p>تعذّر التحقق من الرابط. / This unsubscribe link could not be verified.</p>"
)

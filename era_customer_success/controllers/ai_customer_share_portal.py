import secrets

from odoo import fields, http
from odoo.http import request
from werkzeug.exceptions import Forbidden, NotFound


class AiCustomerSharePortal(http.Controller):

    def _get_authorized_share(self, share_id, access_token):
        share = request.env['cs.ai.customer.share'].sudo().browse(share_id)
        if not share.exists():
            raise NotFound()
        token_ok = bool(access_token and share.access_token and secrets.compare_digest(
            access_token, share.access_token))
        user_ok = bool(not request.env.user._is_public()
                       and request.env.user in share.portal_user_ids)
        if (share.state != 'approved' or not share.portal_enabled
                or share.expires_on < fields.Date.today()
                or not (token_ok or user_ok)):
            raise Forbidden()
        share.write({
            'access_count': share.access_count + 1,
            'last_accessed_on': fields.Datetime.now(),
        })
        return share

    @http.route('/era/customer-sharing/<int:share_id>', type='http', auth='public',
                website=True, sitemap=False)
    def customer_sharing_table(self, share_id, access_token=None, **kwargs):
        share = self._get_authorized_share(share_id, access_token)
        return request.render(
            'era_customer_success.portal_ai_customer_share_table',
            {'share': share, 'access_token': access_token})

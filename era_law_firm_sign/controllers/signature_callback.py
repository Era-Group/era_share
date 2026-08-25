import json
from odoo import http
from odoo.http import request

class SignatureCallbackController(http.Controller):
    @http.route('/legal/signature/callback',type='http',auth='public',methods=['POST'],csrf=False)
    def signature_callback(self,**kwargs):
        payload=request.httprequest.get_data(cache=False)
        try:data=json.loads(payload)
        except (TypeError,ValueError):return request.make_json_response({'error':'invalid_json'},status=400)
        ref=data.get('reference')
        signature=request.httprequest.headers.get('X-Legal-Signature')
        timestamp=request.httprequest.headers.get('X-Legal-Timestamp')
        record=request.env['legal.signature.request'].sudo().search([('external_reference','=',ref)],limit=1)
        if not record:return request.make_json_response({'error':'not_found'},status=404)
        if not record._verify_callback_signature(payload,signature,timestamp):return request.make_json_response({'error':'invalid_signature'},status=403)
        try:
            record._process_callback(data)
        except Exception:
            return request.make_json_response({'error':'invalid_callback'},status=400)
        return request.make_json_response({'ok':True,'state':record.state})

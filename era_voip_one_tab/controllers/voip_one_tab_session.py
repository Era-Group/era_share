from odoo import http
from odoo.http import request


class VoipOneTabSessionController(http.Controller):
    @http.route("/voip/one_tab/acquire", type="jsonrpc", auth="user")
    def acquire(self, tab_id, force=False):
        return request.env["voip.one.tab.session"].acquire(tab_id, force=force)

    @http.route("/voip/one_tab/heartbeat", type="jsonrpc", auth="user")
    def heartbeat(self, tab_id):
        return request.env["voip.one.tab.session"].heartbeat(tab_id)

    @http.route("/voip/one_tab/release", type="jsonrpc", auth="user")
    def release(self, tab_id):
        return request.env["voip.one.tab.session"].release(tab_id)

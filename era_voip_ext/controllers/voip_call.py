import base64

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class VoipCallRecordingController(http.Controller):
    @http.route(
        "/voip_call/recording/<int:call_id>", type="http", auth="user", website=False
    )
    def voip_call_recording(self, call_id):
        # This endpoint only ever receives a voip.call id (see
        # VoipCall._compute_recording_url). Resolve and access-check that call
        # first: a user may only fetch a recording for a call they can read
        # (base voip restricts regular users to their own calls via the
        # user_id record rule). Everything below is bound to *this* call — we
        # never fall back to an unrelated recording.
        call = request.env["voip.call"].browse(call_id).exists()
        if not call:
            return request.not_found()
        try:
            call.check_access("read")
        except AccessError:
            return request.not_found()

        # 1) A linked real-time summary carries the recording. Serve it through
        #    the realtime route, which runs its own access check, rather than
        #    streaming the summary's attachment bytes directly.
        summary = call._find_realtime_summary()
        if summary and summary.attachment_id:
            return request.redirect(f"/realtime_agent/recording/{summary.id}")

        # 2) An audio attachment bound to this call.
        attachment = call._find_recording_attachment()
        if attachment:
            return self._make_inline_attachment_response(attachment)

        # 3) Legacy: a summary recording stored under this call's own
        #    identifiers (attachment name convention). Only redirect through
        #    the access-checked realtime route; never stream the bytes here.
        Attachment = request.env["ir.attachment"].sudo()
        identifiers = {str(call_id)}
        for field_name in ("sip_call_id", "call_id", "external_id"):
            if field_name in call._fields and call[field_name]:
                identifiers.add(str(call[field_name]))
        for ident in identifiers:
            summary_attachment = Attachment.search(
                [
                    ("res_model", "=", "crm.realtime_call_summary"),
                    ("name", "ilike", f"voip-call-{ident}."),
                    ("res_id", "!=", False),
                ],
                order="create_date desc, id desc",
                limit=1,
            )
            if summary_attachment:
                return request.redirect(
                    f"/realtime_agent/recording/{summary_attachment.res_id}"
                )

        return request.not_found()

    def _make_inline_attachment_response(self, attachment):
        try:
            content = base64.b64decode(attachment.datas or b"")
        except Exception:
            return request.not_found()
        mimetype = attachment.mimetype or "application/octet-stream"
        filename = attachment.name or "voip-call.webm"
        headers = [
            ("Content-Type", mimetype),
            ("Content-Disposition", f'inline; filename="{filename}"'),
            ("Content-Security-Policy", "default-src 'self'; media-src 'self'"),
        ]
        return request.make_response(content, headers=headers)

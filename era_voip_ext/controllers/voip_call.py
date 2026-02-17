import base64

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request


class VoipCallRecordingController(http.Controller):
    @http.route(
        "/voip_call/recording/<int:call_id>", type="http", auth="user", website=False
    )
    def voip_call_recording(self, call_id):
        Summary = request.env.get("crm.realtime_call_summary")
        Attachment = request.env["ir.attachment"].sudo()

        summary = Summary.sudo().browse(call_id) if Summary else False
        if summary and summary.exists():
            if summary.attachment_id:
                return self._make_inline_attachment_response(summary.attachment_id)
            return request.redirect(f"/realtime_agent/recording/{summary.id}")

        call = request.env["voip.call"].browse(call_id).exists()
        if call:
            try:
                call.check_access("read")
            except AccessError:
                return request.not_found()

            summary = call._find_realtime_summary()
            if summary and summary.attachment_id:
                return self._make_inline_attachment_response(summary.attachment_id)
            if summary:
                return request.redirect(f"/realtime_agent/recording/{summary.id}")

            attachment = call._find_recording_attachment()
            if attachment:
                return self._make_inline_attachment_response(attachment)

        identifiers = {str(call_id)}
        if call:
            for field_name in ("sip_call_id", "call_id", "external_id"):
                if field_name in call._fields and call[field_name]:
                    identifiers.add(str(call[field_name]))

        for ident in identifiers:
            attachment = Attachment.search(
                [
                    ("res_model", "=", "crm.realtime_call_summary"),
                    ("name", "ilike", f"voip-call-{ident}."),
                ],
                order="create_date desc, id desc",
                limit=1,
            )
            if attachment:
                if attachment.res_id:
                    return request.redirect(
                        f"/realtime_agent/recording/{attachment.res_id}"
                    )
                return self._make_inline_attachment_response(attachment)
            attachment = Attachment.search(
                [
                    ("res_model", "=", "voip.call"),
                    ("res_id", "=", call_id),
                    ("name", "=ilike", "recording.webm"),
                ],
                order="create_date desc, id desc",
                limit=1,
            )
            if attachment:
                return self._make_inline_attachment_response(attachment)
            attachment = Attachment.search(
                [("name", "=ilike", "recording.webm")],
                order="create_date desc, id desc",
                limit=1,
            )
            if attachment:
                return self._make_inline_attachment_response(attachment)

        if Summary and call:
            summary_domain = []
            if "caller_phone" in Summary._fields:
                phone_fields = ("phone", "partner_phone", "contact_phone", "caller_phone")
                phones = [call[name] for name in phone_fields if name in call._fields and call[name]]
                if "partner_id" in call._fields and call.partner_id:
                    if call.partner_id.phone:
                        phones.append(call.partner_id.phone)
                    if call.partner_id.mobile:
                        phones.append(call.partner_id.mobile)
                phones = [phone for phone in phones if phone]
                if phones:
                    summary_domain = [("caller_phone", "in", list(set(phones)))]
            if not summary_domain and "lead_id" in Summary._fields:
                for name in ("lead_id", "opportunity_id"):
                    if name in call._fields and call[name]:
                        summary_domain = [(name, "=", call[name].id)]
                        break
            if summary_domain:
                summary = Summary.sudo().search(
                    summary_domain, order="create_date desc, id desc", limit=1
                )
                if summary and summary.attachment_id:
                    return self._make_inline_attachment_response(summary.attachment_id)
                if summary:
                    return request.redirect(f"/realtime_agent/recording/{summary.id}")

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

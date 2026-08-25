from werkzeug.exceptions import BadRequest, Forbidden

from odoo import http
from odoo.http import Response, request

from odoo.addons.voip_ai.controllers.voip_ai_controller import (
    TRANSCRIPTION_MAX_FILE_SIZE,
    VoipAiController,
)


class VoipAiControllerExt(VoipAiController):
    @http.route(
        "/voip_ai/transcribe/<models('voip.call'):call>",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=True,
    )
    def transcribe_call(self, call, ufile):
        call.ensure_one()
        if not ufile:
            raise BadRequest()
        if call.transcription_status != "no_audio":
            raise Forbidden()
        call.with_user(request.session.uid).check_access("read")

        call_sudo = call.sudo()
        request.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(%s, %s)",
            (97123, int(call_sudo.id)),
        )
        lock_row = request.env.cr.fetchone()
        if not (lock_row and lock_row[0]):
            return request.make_response(
                "Call is already being processed",
                status=409,
                headers=[("Content-Type", "text/plain")],
            )

        recording_raw = ufile.read()
        if len(recording_raw) > TRANSCRIPTION_MAX_FILE_SIZE:
            call_sudo._safe_write(call_sudo, {"transcription_status": "too_big_to_process"})
            return request.make_response(
                "Recording too large",
                status=413,
                headers=[("Content-Type", "text/plain")],
            )

        request.env["ir.attachment"].sudo().create(
            {
                "name": "call_recording.ogg",
                "res_model": "voip.call",
                "res_id": call_sudo.id,
                "type": "binary",
                "mimetype": "audio/ogg",
                "raw": recording_raw,
            }
        )
        call_sudo._finalize_if_ongoing()
        request.env.ref("voip_ai.ir_cron_transcribe_recent_voip_call").sudo()._trigger()
        return Response(status=200)

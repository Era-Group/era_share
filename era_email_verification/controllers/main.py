"""Public webhook that receives result pushes from the Email Verifier.

Security model (this is an unauthenticated, internet-facing write endpoint, so
every layer matters):

* the caller must send ``X-EV-Timestamp`` and ``X-EV-Signature`` where the
  signature is ``sha256=HMAC(secret, "<timestamp>.<raw-body>")`` and ``secret``
  is the per-batch ``callback_secret`` generated when the job was submitted;
* the timestamp must be within a small window (replay protection);
* the signature is compared in constant time;
* on success the endpoint only ever attaches results to the ONE batch named by
  ``job_id`` — it writes verification fields on that batch's partners/contacts
  and, per the configured policy, may add addresses to Odoo's (global)
  ``mail.blacklist``; it cannot touch any unrelated record.

Every authentication failure — unknown job, no secret, or bad signature —
returns an identical 401 so the endpoint is not a job-existence oracle.
"""
import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Tolerated clock skew / replay window, in seconds.
_SIGN_WINDOW = 300
# Hard cap on accepted body size (a 50-result chunk is a few KB).
_MAX_BODY = 5 * 1024 * 1024


class EmailVerificationWebhook(http.Controller):

    @http.route("/era_email_verification/webhook", type="http", auth="public",
                methods=["POST"], csrf=False)
    def receive(self, **_kw):
        req = request.httprequest
        raw = req.get_data()  # exact bytes — HMAC is computed over these
        if len(raw) > _MAX_BODY:
            return _json(413, {"error": "too_large"})

        timestamp = req.headers.get("X-EV-Timestamp", "")
        signature = req.headers.get("X-EV-Signature", "")
        if not timestamp or not signature:
            return _json(400, {"error": "missing_signature"})
        try:
            if abs(int(time.time()) - int(timestamp)) > _SIGN_WINDOW:
                return _json(401, {"error": "stale_timestamp"})
        except (TypeError, ValueError):
            return _json(400, {"error": "bad_timestamp"})

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _json(400, {"error": "bad_json"})
        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return _json(400, {"error": "missing_job_id"})

        batch = request.env["email.verification.batch"].sudo().search(
            [("job_id", "=", job_id)], limit=1)
        # Uniform 401 for every auth failure so existence can't be probed.
        if not batch or not batch.callback_secret or not signature.isascii():
            return _json(401, {"error": "unauthorized"})

        expected = "sha256=" + hmac.new(
            batch.callback_secret.encode("utf-8"),
            timestamp.encode("ascii") + b"." + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            _logger.warning("Email verification webhook: bad signature for job %s", job_id)
            return _json(401, {"error": "unauthorized"})

        items = payload.get("items") or []
        if not isinstance(items, list):
            return _json(400, {"error": "bad_items"})
        done = bool(payload.get("done"))
        try:
            imported = batch._apply_pushed_results(items, done=done)
        except Exception:  # noqa: BLE001 - never surface internals to the caller
            _logger.exception("Email verification webhook: apply failed for job %s", job_id)
            return _json(500, {"error": "apply_failed"})
        return _json(200, {"ok": True, "imported": imported})


def _json(status, data):
    return request.make_json_response(data, status=status)

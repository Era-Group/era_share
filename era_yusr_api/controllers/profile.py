# -*- coding: utf-8 -*-
"""
Profile endpoints:
  GET  /api/yusr/profile   - Get current employee profile
  PUT  /api/yusr/profile   - Request profile update (creates change request for HR approval)
"""
import logging

from odoo import http, SUPERUSER_ID, _
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from .base import ok, err, get_json_payload, yusr_authenticated

_logger = logging.getLogger(__name__)


class YusrProfileController(http.Controller):

    @http.route(
        '/api/yusr/profile',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def get_profile(self, employee=None, **kwargs):
        return ok(_serialize_full_profile(employee))

    @http.route(
        '/api/yusr/profile/avatar',
        type='http', auth='none', methods=['PUT', 'POST'], csrf=False
    )
    @yusr_authenticated
    def upload_avatar(self, employee=None, **kwargs):
        """Let an employee replace their own photo from the mobile app.

        Body: { "image_base64": "<base64>" }

        Unlike the text profile fields (which go through an HR review
        queue via chatter), the photo is written directly — it's a
        self-service field and HR can moderate via Odoo's normal
        chatter/audit trail. Writing to image_1920 lets Odoo's
        computed smaller sizes (image_1024/512/256/128) regenerate
        automatically.
        """
        import base64
        payload = get_json_payload()
        raw = payload.get('image_base64') or ''
        # Allow the client to send a data URI — strip the prefix.
        if raw.startswith('data:'):
            comma = raw.find(',')
            if comma >= 0:
                raw = raw[comma + 1:]
        # Drop any whitespace/newlines the client may have inserted
        # (some libraries wrap base64 at 76 chars per RFC 2045).
        raw = ''.join(raw.split())
        if not raw:
            return err(_("image_base64 is required."), status=400, code='NO_IMAGE')
        # Sanity-check it decodes and isn't absurdly large (8 MB cap).
        # Use validate=False to tolerate the common case of extra
        # padding or URL-safe chars; the size check below still
        # catches garbage.
        try:
            decoded = base64.b64decode(raw, validate=False)
        except Exception:
            return err(_("Image is not valid base64."), status=400, code='BAD_IMAGE')
        if len(decoded) > 8 * 1024 * 1024:
            return err(_("Image too large (max 8 MB)."), status=413, code='IMAGE_TOO_LARGE')
        if len(decoded) < 100:
            return err(_("Image is empty or too small."), status=400, code='IMAGE_TOO_SMALL')

        # with_user(SUPERUSER_ID) — not just sudo() — because hr.employee's
        # image compute chain calls self.env.user on some fields; under
        # auth='none' .sudo() flips su=True but leaves env.user empty and
        # the compute blows up mid-write. Same pattern used by leaves.py.
        #
        # savepoint(): if the Pillow resize or a constraint raises after
        # the image_1920 row update, roll back so we don't leave a
        # half-written record (partial sizes, broken computed fields).
        emp = employee.with_user(SUPERUSER_ID)
        try:
            with request.env.cr.savepoint():
                emp.write({'image_1920': raw})
        except (AccessError, UserError, ValidationError) as e:
            _logger.warning("Avatar write rejected for employee %s: %s", employee.id, e)
            return err(str(e), status=400, code='IMAGE_WRITE_REJECTED')
        except Exception as e:
            # Pillow failures (unsupported format, truncated), DB size
            # limits, storage errors — surface them instead of hiding
            # behind a generic 500.
            _logger.exception("Avatar write failed for employee %s", employee.id)
            return err(str(e) or _("Could not save the image."),
                       status=500, code='IMAGE_WRITE_FAILED')

        # Chatter post is best-effort: we already saved the image, so a
        # mail.thread hiccup shouldn't fail the whole request.
        try:
            emp.message_post(
                body=_("Photo updated via the Yusr mobile app."),
                subject=_("Profile Photo Updated"),
                message_type='comment',
            )
        except Exception:
            _logger.exception("Avatar chatter post failed for employee %s (image was saved)",
                              employee.id)

        return ok({'message': _("Photo updated.")})

    @http.route(
        '/api/yusr/profile',
        type='http', auth='none', methods=['PUT'], csrf=False
    )
    @yusr_authenticated
    def update_profile(self, employee=None, **kwargs):
        """
        Employees can't directly edit protected fields. We log the request
        as a chatter message to hr.employee. HR reviews and applies changes.
        """
        payload = get_json_payload()
        allowed_request_fields = [
            'mobile_phone', 'work_phone', 'private_phone',
            'emergency_contact', 'emergency_phone',
            'private_street', 'private_city', 'private_zip',
        ]
        requested = {k: v for k, v in payload.items() if k in allowed_request_fields}
        if not requested:
            return err("No valid fields to update.", status=400)

        body = "Profile update request from Yusr mobile app:<ul>"
        for k, v in requested.items():
            body += f"<li><b>{k}</b>: {v}</li>"
        body += "</ul>"
        employee.sudo().message_post(
            body=body,
            subject="Profile Update Request",
            message_type='comment',
        )
        return ok({'message': 'Update request submitted for HR approval.'})


def _serialize_full_profile(emp):
    return {
        'id': emp.id,
        'name': emp.name,
        'login_id': emp.employee_login_id,
        'job_title': emp.job_title,
        'department': emp.department_id.name if emp.department_id else None,
        'manager': emp.parent_id.name if emp.parent_id else None,
        'company': emp.company_id.name if emp.company_id else None,
        'work_email': emp.work_email,
        'work_phone': emp.work_phone,
        'mobile_phone': emp.mobile_phone,
        'contract_start_date': emp.first_contract_date if 'first_contract_date' in emp._fields else None,
        'identification_id': emp.identification_id,
        'avatar_url': f"/web/image/hr.employee/{emp.id}/image_256",
        # Data URI so the mobile <Image> can render without a second
        # authenticated request. Odoo's /web/image route falls back to
        # a placeholder for unauthenticated users on hr.employee
        # (employee ACLs block the public user), so we can't just hand
        # the mobile that URL. Embedding the bytes in the profile
        # response keeps the happy path to one round-trip. None when
        # no photo is set so the mobile can render initials instead.
        'avatar_image': _encode_avatar(emp),
    }


def _encode_avatar(emp):
    """Return a data URI with the employee's 256x256 photo, or None."""
    img = emp.sudo().image_256
    if not img:
        return None
    # Odoo stores binary fields as bytes of base64-encoded data. Decode
    # to a str for JSON so the response doesn't need a custom encoder.
    if isinstance(img, bytes):
        img = img.decode('ascii')
    return f"data:image/png;base64,{img}"

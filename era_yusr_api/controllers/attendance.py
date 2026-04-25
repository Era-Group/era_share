# -*- coding: utf-8 -*-
"""
Attendance endpoints:
  POST /api/yusr/attendance/checkin
  POST /api/yusr/attendance/checkout
  GET  /api/yusr/attendance/records?month=YYYY-MM
  GET  /api/yusr/attendance/status
"""
import math
from datetime import datetime, date, timedelta

from odoo import http, fields
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


def _haversine(lat1, lon1, lat2, lon2):
    """Distance in meters between two GPS points."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


class YusrAttendanceController(http.Controller):

    @http.route(
        '/api/yusr/attendance/checkin',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def checkin(self, employee=None, **kwargs):
        payload = get_json_payload()
        lat = payload.get('latitude')
        lon = payload.get('longitude')

        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False),
        ], limit=1)
        if open_att:
            return ok({
                'attendance_id': open_att.id,
                'check_in': open_att.check_in,
                'message': 'Already checked in.',
                'already_checked_in': True,
            })

        # Geofence check
        allowed, distance = self._check_geofence(employee, lat, lon)
        if not allowed and not payload.get('override_reason'):
            return err(
                "You are outside the allowed location.",
                status=403, code='OUTSIDE_GEOFENCE',
                distance_meters=round(distance) if distance else None,
            )

        vals = {
            'employee_id': employee.id,
            'check_in': fields.Datetime.now(),
        }
        if lat is not None and lon is not None:
            vals.update({
                'in_latitude': lat,
                'in_longitude': lon,
            })
        att = request.env['hr.attendance'].sudo().create(vals)

        if payload.get('override_reason'):
            att.message_post(
                body=f"Check-in outside geofence. Reason: {payload['override_reason']}"
            )

        return ok({
            'attendance_id': att.id,
            'check_in': att.check_in,
            'message': 'Checked in successfully.',
        })

    @http.route(
        '/api/yusr/attendance/checkout',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def checkout(self, employee=None, **kwargs):
        payload = get_json_payload()
        lat = payload.get('latitude')
        lon = payload.get('longitude')

        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False),
        ], limit=1)
        if not open_att:
            return err("No open attendance found.", status=404, code='NO_OPEN_ATTENDANCE')

        vals = {'check_out': fields.Datetime.now()}
        if lat is not None and lon is not None:
            vals.update({
                'out_latitude': lat,
                'out_longitude': lon,
            })
        open_att.sudo().write(vals)

        return ok({
            'attendance_id': open_att.id,
            'check_in': open_att.check_in,
            'check_out': open_att.check_out,
            'worked_hours': open_att.worked_hours,
            'message': 'Checked out successfully.',
        })

    @http.route(
        '/api/yusr/attendance/status',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def status(self, employee=None, **kwargs):
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False),
        ], limit=1)
        today_atts = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', datetime.combine(date.today(), datetime.min.time())),
        ])
        total_today = sum(a.worked_hours or 0 for a in today_atts)

        return ok({
            'checked_in': bool(open_att),
            'check_in_time': open_att.check_in if open_att else None,
            'today_total_hours': round(total_today, 2),
            'attendance_count_today': len(today_atts),
        })

    @http.route(
        '/api/yusr/attendance/records',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def records(self, employee=None, **kwargs):
        month = request.httprequest.args.get('month')  # YYYY-MM
        try:
            if month:
                y, m = month.split('-')
                start = date(int(y), int(m), 1)
            else:
                today = date.today()
                start = date(today.year, today.month, 1)
            # last day of month
            if start.month == 12:
                end = date(start.year + 1, 1, 1)
            else:
                end = date(start.year, start.month + 1, 1)
        except Exception:
            return err("Invalid month format. Use YYYY-MM.", status=400)

        atts = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_in', '>=', datetime.combine(start, datetime.min.time())),
            ('check_in', '<', datetime.combine(end, datetime.min.time())),
        ], order='check_in asc')

        # Skip placeholder rows: closed attendances whose duration is
        # effectively zero (e.g. check_in 03:00:00 / check_out
        # 03:00:01). These are bulk-imported junk in the Odoo DB and
        # render on the mobile calendar as real check-ins, making
        # non-working days look attended. Kept open attendances
        # (check_out is False) — those are legitimate in-progress
        # sessions.
        PLACEHOLDER_THRESHOLD_SECONDS = 60
        records = []
        for a in atts:
            if not a.check_in:
                continue
            if a.check_out:
                duration = (a.check_out - a.check_in).total_seconds()
                if duration < PLACEHOLDER_THRESHOLD_SECONDS:
                    continue
            records.append({
                'id': a.id,
                'date': a.check_in.date().isoformat(),
                'check_in': a.check_in,
                'check_out': a.check_out,
                'worked_hours': round(a.worked_hours or 0, 2),
            })

        total = sum(r['worked_hours'] for r in records)

        return ok({
            'month': f"{start.year:04d}-{start.month:02d}",
            'records': records,
            'total_hours': round(total, 2),
            'days_present': len(set(r['date'] for r in records if r['date'])),
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _check_geofence(self, employee, lat, lon):
        """Return (allowed: bool, distance_meters: float | None).

        Evaluation order:
          1. Global kill switch `era_yusr_api.geofence_enabled` (default
             True) — if off, skip entirely.
          2. hr.attendance.location records assigned to this employee
             (from era_mobile_hr_api). Each location can have its own
             `tolerance_meters`; fall back to the global radius if not
             set. Employee is allowed if within ANY assigned location.
          3. Company partner lat/lon — legacy behavior. Used only if
             no hr.attendance.location records apply to the employee.
          4. If nothing is configured at all, allow (don't lock users
             out because HR hasn't filled in coordinates yet).
        """
        ICP = request.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('era_yusr_api.geofence_enabled', 'True') == 'True'
        if not enabled:
            return True, None
        if lat is None or lon is None:
            return False, None

        default_radius = int(ICP.get_param('era_yusr_api.geofence_radius', '200'))
        flat, flon = float(lat), float(lon)

        # Step 2: hr.attendance.location records (from era_mobile_hr_api).
        # Gate on the model existing — this module shouldn't hard-depend
        # on era_mobile_hr_api being installed.
        closest = None
        if 'hr.attendance.location' in request.env:
            Loc = request.env['hr.attendance.location'].sudo()
            # Per-employee assignment; if no m2m match, don't fall back
            # to "any location in the DB" (that could accidentally let
            # an employee check in at another branch). We explicitly
            # move on to the company-partner check.
            locations = Loc.search([
                ('active', '=', True),
                ('employee_ids', 'in', employee.id),
            ])
            for loc in locations:
                if not loc.latitude or not loc.longitude:
                    continue
                tol = (
                    getattr(loc, 'tolerance_meters', False)
                    or default_radius
                )
                d = _haversine(flat, flon, loc.latitude, loc.longitude)
                if closest is None or d < closest:
                    closest = d
                if d <= tol:
                    return True, d
            if closest is not None:
                # Employee had assigned locations but is outside all of
                # them — reject with the nearest distance so the mobile
                # can show "you are Xm from the nearest site".
                return False, closest

        # Step 3: fall back to res.company partner coordinates.
        company = employee.company_id
        partner = company.partner_id if company else False
        if partner and partner.partner_latitude and partner.partner_longitude:
            d = _haversine(
                flat, flon,
                partner.partner_latitude, partner.partner_longitude,
            )
            return d <= default_radius, d

        # Step 4: nothing configured. Allow (don't lock people out).
        return True, None

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
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
    )
    @yusr_authenticated
    def checkin(self, employee=None, **kwargs):
        payload = get_json_payload()
        lat = payload.get('latitude')
        lon = payload.get('longitude')

        # Close any open attendance first (defensive)
        open_att = request.env['hr.attendance'].sudo().search([
            ('employee_id', '=', employee.id),
            ('check_out', '=', False),
        ], limit=1)
        if open_att:
            return err("You are already checked in.", status=409, code='ALREADY_CHECKED_IN')

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
        type='http', auth='public', methods=['POST'], csrf=False, cors='*'
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
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
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
        type='http', auth='public', methods=['GET'], csrf=False, cors='*'
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

        records = [{
            'id': a.id,
            'date': a.check_in.date().isoformat() if a.check_in else None,
            'check_in': a.check_in,
            'check_out': a.check_out,
            'worked_hours': round(a.worked_hours or 0, 2),
        } for a in atts]

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
        """Return (allowed: bool, distance_meters: float | None)."""
        ICP = request.env['ir.config_parameter'].sudo()
        enabled = ICP.get_param('era_yusr_api.geofence_enabled', 'True') == 'True'
        if not enabled:
            return True, None
        if lat is None or lon is None:
            return False, None

        radius = int(ICP.get_param('era_yusr_api.geofence_radius', '200'))

        # Try to get branch/company coordinates
        # Assumes res.company has partner_id with partner_latitude/partner_longitude
        company = employee.company_id
        partner = company.partner_id
        if not partner.partner_latitude or not partner.partner_longitude:
            # Geofence enabled but no coords set -> allow but log
            return True, None

        distance = _haversine(
            float(lat), float(lon),
            partner.partner_latitude, partner.partner_longitude,
        )
        return distance <= radius, distance

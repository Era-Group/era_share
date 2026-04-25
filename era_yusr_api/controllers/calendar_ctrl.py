# -*- coding: utf-8 -*-
"""
Employee calendar endpoints:
  GET /api/yusr/calendar?month=YYYY-MM
Returns unified events: approved leaves, public holidays, company events.
"""
from datetime import datetime, date

from odoo import http
from odoo.http import request

from .base import ok, err, yusr_authenticated


class YusrCalendarController(http.Controller):

    @http.route(
        '/api/yusr/calendar',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def calendar(self, employee=None, **kwargs):
        month = request.httprequest.args.get('month')
        try:
            if month:
                y, m = month.split('-')
                start = date(int(y), int(m), 1)
            else:
                today = date.today()
                start = date(today.year, today.month, 1)
            end = date(start.year + 1, 1, 1) if start.month == 12 \
                else date(start.year, start.month + 1, 1)
        except Exception:
            return err("Invalid month format. Use YYYY-MM.", status=400)

        events = []

        # Approved leaves
        leaves = request.env['hr.leave'].sudo().search([
            ('employee_id', '=', employee.id),
            ('state', '=', 'validate'),
            ('request_date_from', '<', end),
            ('request_date_to', '>=', start),
        ])
        for l in leaves:
            events.append({
                'type': 'leave',
                'title': l.holiday_status_id.name,
                'date_from': l.request_date_from,
                'date_to': l.request_date_to,
                'color': '#3498db',
            })

        # Public holidays (resource.calendar.leaves with resource_id = False)
        calendar_id = employee.resource_calendar_id.id
        if calendar_id:
            holidays = request.env['resource.calendar.leaves'].sudo().search([
                ('calendar_id', '=', calendar_id),
                ('resource_id', '=', False),
                ('date_from', '<', datetime.combine(end, datetime.min.time())),
                ('date_to', '>=', datetime.combine(start, datetime.min.time())),
            ])
            for h in holidays:
                events.append({
                    'type': 'holiday',
                    'title': h.name or 'Public Holiday',
                    'date_from': h.date_from.date() if h.date_from else None,
                    'date_to': h.date_to.date() if h.date_to else None,
                    'color': '#e74c3c',
                })

        # Personal calendar events
        if 'calendar.event' in request.env and employee.user_id:
            cal_events = request.env['calendar.event'].sudo().with_user(employee.user_id).search([
                ('partner_ids', 'in', employee.user_id.partner_id.id),
                ('start', '<', datetime.combine(end, datetime.min.time())),
                ('stop', '>=', datetime.combine(start, datetime.min.time())),
            ])
            for c in cal_events:
                events.append({
                    'type': 'event',
                    'title': c.name,
                    'date_from': c.start,
                    'date_to': c.stop,
                    'color': '#2ecc71',
                    'location': c.location,
                })

        return ok({
            'month': f"{start.year:04d}-{start.month:02d}",
            'events': events,
        })

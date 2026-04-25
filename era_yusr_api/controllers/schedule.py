# -*- coding: utf-8 -*-
"""
Working schedule endpoints:
  GET /api/yusr/schedule          - Weekly schedule from resource.calendar
"""
from odoo import http
from odoo.http import request

from .base import ok, err, yusr_authenticated


class YusrScheduleController(http.Controller):

    @http.route(
        '/api/yusr/schedule',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def schedule(self, employee=None, **kwargs):
        cal = employee.resource_calendar_id
        if not cal:
            return ok({'calendar_name': None, 'attendances': []})

        days_map = {
            '0': 'Monday', '1': 'Tuesday', '2': 'Wednesday',
            '3': 'Thursday', '4': 'Friday', '5': 'Saturday', '6': 'Sunday',
        }
        attendances = [{
            'day': days_map.get(a.dayofweek, a.dayofweek),
            'day_index': int(a.dayofweek),
            'hour_from': a.hour_from,
            'hour_to': a.hour_to,
            'name': a.name,
            'day_period': a.day_period,
        } for a in cal.attendance_ids]

        return ok({
            'calendar_name': cal.name,
            'hours_per_day': cal.hours_per_day,
            'tz': cal.tz,
            'attendances': attendances,
        })

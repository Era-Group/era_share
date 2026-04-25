# -*- coding: utf-8 -*-
"""
Leaves endpoints:
  GET  /api/yusr/leaves/balances
  GET  /api/yusr/leaves/requests
  POST /api/yusr/leaves/requests
  POST /api/yusr/leaves/requests/<id>/cancel
"""
from datetime import datetime

from odoo import http, fields, SUPERUSER_ID, _
from odoo.exceptions import ValidationError, UserError
from odoo.http import request

from .base import ok, err, get_json_payload, yusr_authenticated


class YusrLeavesController(http.Controller):

    @http.route(
        '/api/yusr/leaves/balances',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def balances(self, employee=None, **kwargs):
        """Return leave balances grouped by leave type.

        Visibility rules:
          - `active = True`
          - `employee_requests = True` (Boolean in Odoo 19; was a
            Selection in 18) — types seeded by localization addons
            as HR-only have this False and are hidden
          - `yusr_visible = True` (per-type override; default True,
            so HR can hide individual types from the mobile without
            archiving them for the rest of Odoo)
          - scoped to employee's company AND country: country-agnostic
            types (`country_id = False`) pass, plus types whose
            `country_id` matches the company country. This drops the
            Belgium-localized types (`European Time Off`,
            `Recovery Bank Holiday`, `Credit Time`, etc.) that
            `hr_holidays` seeds globally but tags with `country_id =
            base.be`.

        Types with `requires_allocation = False` (e.g. Unpaid Leave)
        have no allocation cap; `unlimited=True` labels them "∞" in
        the mobile instead of a misleading `N/0`.
        """
        domain = self._yusr_leave_type_domain(employee)
        types = request.env['hr.leave.type'].sudo().search(domain)
        Allocation = request.env['hr.leave.allocation'].sudo()
        Leave = request.env['hr.leave'].sudo()
        data = []
        for lt in types:
            taken = sum(
                lr.number_of_days for lr in Leave.search([
                    ('employee_id', '=', employee.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', '=', 'validate'),
                ])
            )
            if lt.requires_allocation:
                allocations = Allocation.search([
                    ('employee_id', '=', employee.id),
                    ('holiday_status_id', '=', lt.id),
                    ('state', '=', 'validate'),
                ])
                total_allocated = sum(a.number_of_days for a in allocations)
                remaining = total_allocated - taken
                unlimited = False
            else:
                total_allocated = 0
                remaining = 0
                unlimited = True

            data.append({
                'id': lt.id,
                'name': lt.name,
                'color': lt.color,
                'allocated': total_allocated,
                'taken': taken,
                'remaining': remaining,
                'unit': 'days',
                'unlimited': unlimited,
                'requires_allocation': bool(lt.requires_allocation),
            })
        return ok({'balances': data})

    @http.route(
        '/api/yusr/leaves/types',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def leave_types(self, employee=None, **kwargs):
        """List the leave types the employee can request.

        Same visibility rules as /balances — see _yusr_leave_type_domain.

        Name is resolved via env.context['lang'] (set by
        apply_request_lang), so translated type names come back in the
        caller's language automatically.

        Exposes `request_unit` so the mobile app knows whether to
        collect full-day, half-day, or hour-range inputs, and
        `requires_allocation` so the app can render the "unlimited"
        label correctly for types without a cap.
        """
        types = request.env['hr.leave.type'].sudo().search(
            self._yusr_leave_type_domain(employee)
        )

        return ok({
            'types': [{
                'id': t.id,
                'name': t.name,
                'requires_allocation': bool(t.requires_allocation),
                'request_unit': t.request_unit,
                'color': t.color,
            } for t in types]
        })

    # ------------------------------------------------------------------
    # Shared leave-type domain
    # ------------------------------------------------------------------
    def _yusr_leave_type_domain(self, employee):
        """Domain shared by /balances and /types so both endpoints
        return exactly the same list.

        Both `employee_requests` and `requires_allocation` became
        Boolean in Odoo 19 (were Selection 'yes'/'no' in 18). Use True
        here, not the legacy string.

        `country_id` gate: include types tagged for the company's
        country, plus country-agnostic types. This keeps
        localization-seeded types (Belgian `European Time Off`,
        `Credit Time`, etc.) out of the mobile when the company's
        country isn't Belgium.
        """
        domain = [
            ('active', '=', True),
            ('employee_requests', '=', True),
            ('yusr_visible', '=', True),
        ]
        company = employee.company_id
        if company:
            domain += [
                '|', ('company_id', '=', False), ('company_id', '=', company.id),
            ]
            country = company.country_id
            if country:
                domain += [
                    '|',
                    ('country_id', '=', False),
                    ('country_id', '=', country.id),
                ]
            else:
                # Company has no country → only accept country-agnostic
                # types (safer than returning every localization).
                domain += [('country_id', '=', False)]
        return domain

    @http.route(
        '/api/yusr/leaves/requests',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def list_requests(self, employee=None, **kwargs):
        # with_user(SUPERUSER_ID), not bare sudo(): the `name` field on
        # hr.leave is a compute with compute_sudo=False that calls
        # self.env.user.has_group(...). Under auth='none', .sudo() flips
        # su=True but leaves env.user as an empty recordset, and
        # has_group() on that raises — 500s the endpoint. Same reason
        # create_request below uses with_user(SUPERUSER_ID).
        leaves = request.env['hr.leave'].with_user(SUPERUSER_ID).search(
            [('employee_id', '=', employee.id)],
            order='create_date desc', limit=100,
        )
        return ok({'requests': [_serialize_leave(l) for l in leaves]})

    @http.route(
        '/api/yusr/leaves/requests',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def create_request(self, employee=None, **kwargs):
        payload = get_json_payload()
        required = ['holiday_status_id', 'date_from', 'date_to']
        for f in required:
            if not payload.get(f):
                return err(
                    _("Field '%s' is required.") % f,
                    status=400, code='FIELD_REQUIRED',
                )

        try:
            date_from = datetime.fromisoformat(payload['date_from'])
            date_to = datetime.fromisoformat(payload['date_to'])
        except ValueError:
            return err(
                _("Invalid date format. Use ISO 8601."),
                status=400, code='BAD_DATE',
            )

        # Reject swapped date range BEFORE the ORM call. Without this
        # Postgres's hr_leave_date_check3 constraint trips and the
        # request surfaces as an opaque 500 in the logs. The mobile has
        # no client-side check either (date pickers can produce either
        # order when the user picks end first).
        if date_to.date() < date_from.date():
            return err(
                _("End date must be on or after the start date."),
                status=400, code='BAD_DATE_RANGE',
            )

        holiday_status_id = int(payload['holiday_status_id'])
        # See note on `with_user(SUPERUSER_ID)` below — same rationale here.
        leave_type = (
            request.env['hr.leave.type']
            .with_user(SUPERUSER_ID)
            .browse(holiday_status_id)
            .exists()
        )
        if not leave_type or not leave_type.active:
            return err(
                _("Leave type not found."),
                status=404, code='LEAVE_TYPE_NOT_FOUND',
            )

        # ---------------- Overlap guard ----------------
        # Reject the request BEFORE calling create() when the employee
        # already has a non-cancelled leave covering any day in
        # [date_from, date_to]. Odoo's own validator fires after create
        # and leaves a stray draft record behind — the mobile dev saw
        # this as "leave is submitted but an error is shown".
        overlap = (
            request.env['hr.leave']
            .with_user(SUPERUSER_ID)
            .search([
                ('employee_id', '=', employee.id),
                ('state', 'not in', ('cancel', 'refuse')),
                ('request_date_from', '<=', date_to.date()),
                ('request_date_to', '>=', date_from.date()),
            ], limit=1)
        )
        if overlap:
            return err(
                _("You already have a leave request during this period."),
                status=409, code='LEAVE_OVERLAP',
                overlap_request_id=overlap.id,
                overlap_state=overlap.state,
            )

        vals = {
            'employee_id': employee.id,
            'holiday_status_id': holiday_status_id,
            'request_date_from': date_from.date(),
            'request_date_to': date_to.date(),
            'name': payload.get('reason') or _("Leave request (Yusr)"),
        }

        # ---------------- Hourly / half-day / day ----------------
        if leave_type.request_unit == 'hour':
            hour_from = payload.get('hour_from')
            hour_to = payload.get('hour_to')
            if hour_from is None or hour_to is None:
                return err(
                    _("This leave type is measured in hours. "
                      "Please provide hour_from and hour_to."),
                    status=400, code='HOURS_REQUIRED',
                )
            try:
                hour_from_f = float(hour_from)
                hour_to_f = float(hour_to)
            except (TypeError, ValueError):
                return err(
                    _("hour_from and hour_to must be decimal hours "
                      "(e.g. 9.5 for 09:30)."),
                    status=400, code='BAD_HOURS',
                )
            if not (0 <= hour_from_f < 24) or not (0 < hour_to_f <= 24):
                return err(
                    _("Hours must be between 0 and 24."),
                    status=400, code='BAD_HOURS',
                )
            if hour_to_f <= hour_from_f:
                return err(
                    _("hour_to must be greater than hour_from."),
                    status=400, code='BAD_HOURS',
                )
            vals.update({
                'request_unit_hours': True,
                'request_hour_from': hour_from_f,
                'request_hour_to': hour_to_f,
            })
        elif payload.get('half_day'):
            vals['request_unit_half'] = True

        # Use with_user(SUPERUSER_ID) — not bare sudo() — because hr.leave
        # internals (e.g. _check_double_validation_rules) call
        # self.env.user.has_group(...). With auth='none' the request env has
        # no uid, and .sudo() only flips su=True without setting one, so
        # env.user resolves to an empty recordset and ensure_one() blows up.
        # Default state on create is already 'confirm' (To Approve) in
        # Odoo 19, and hr.leave no longer exposes action_confirm().
        #
        # Wrap the create() in a SQL savepoint so that when Odoo's own
        # validators raise (insufficient allocation, blocked date, company
        # validation, etc.) the partially-inserted hr_leave row is rolled
        # back. Without the savepoint, catching the exception in Python
        # lets Odoo's request-end commit ship the half-baked record to
        # the DB, which is exactly the "the request was opened anyway"
        # bug HR noticed.
        try:
            with request.env.cr.savepoint():
                leave = request.env['hr.leave'].with_user(SUPERUSER_ID).create(vals)
        except (ValidationError, UserError) as e:
            # Odoo's own validators (allocation exhaustion, blocked dates,
            # etc.) speak whatever language env.context['lang'] is set to —
            # which apply_request_lang() already configured. Surface the
            # message directly to the client.
            return err(str(e), status=400, code='LEAVE_REJECTED')

        return ok({
            'request_id': leave.id,
            'state': leave.state,
            'message': _("Leave request submitted."),
        }, status=201)

    @http.route(
        '/api/yusr/leaves/requests/<int:leave_id>/cancel',
        type='http', auth='none', methods=['POST'], csrf=False
    )
    @yusr_authenticated
    def cancel_request(self, leave_id, employee=None, **kwargs):
        leave = request.env['hr.leave'].with_user(SUPERUSER_ID).browse(leave_id).exists()
        if not leave or leave.employee_id.id != employee.id:
            return err(_("Request not found."), status=404, code='NOT_FOUND')
        if leave.state not in ('draft', 'confirm'):
            return err(
                _("Only pending requests can be cancelled."),
                status=400, code='NOT_CANCELLABLE',
            )
        # Same savepoint pattern as create_request: if action_refuse
        # raises, we don't want whatever it did write before the raise
        # (state transitions, message posts, etc.) to leak into the
        # final commit.
        try:
            with request.env.cr.savepoint():
                leave.action_refuse()
        except (ValidationError, UserError) as e:
            return err(str(e), status=400, code='CANCEL_REJECTED')
        return ok({'message': _("Request cancelled.")})


def _serialize_leave(l):
    return {
        'id': l.id,
        'name': l.name,
        'type': l.holiday_status_id.name,
        'type_id': l.holiday_status_id.id,
        'date_from': l.request_date_from,
        'date_to': l.request_date_to,
        'number_of_days': l.number_of_days,
        'state': l.state,
        'state_label': dict(l._fields['state'].selection).get(l.state),
        'create_date': l.create_date,
    }

# -*- coding: utf-8 -*-
"""
Recent activity feed for the Yusr mobile home screen:
  GET /api/yusr/notifications?limit=20

Merges the employee's recent leave state changes, new payslips, and
expense state changes into one list sorted by recency, then formats
each event to match the mobile's NotificationItem contract:

    { id, title, body, time, type, unread }

`type` is one of the mobile enum values 'leave' | 'expense' | 'message'.
Payslip events use 'message' because the mobile type enum has no
'payslip' slot; the title text disambiguates them for the user.

`unread` uses a simple recency heuristic (last 24h). A proper per-user
read marker is left for a follow-up — it needs a new field on
hr.employee and a companion /mark-read endpoint, which is more scope
than this feed warrants today.

Translations are embedded inline as `{en, ar}` tuples rather than
going through `_()`. Odoo 19's default `-u` does NOT reload .po files
(that needs --i18n-overwrite), which made the Arabic strings in
ar_SA.po silently not take effect. Inline strings sidestep the whole
translation-loading machinery: change them in code, bump the
manifest, ship.
"""
import logging
from datetime import timedelta

from odoo import http, fields, SUPERUSER_ID
from odoo.http import request

from .base import ok, err, yusr_authenticated

_logger = logging.getLogger(__name__)

_UNREAD_WINDOW_HOURS = 24
_LOOKBACK_DAYS = 30


def _t(label_key, context_lang):
    """Pick the Arabic or English variant for a label key."""
    ar = context_lang and context_lang.lower().startswith('ar')
    return _LABELS[label_key]['ar' if ar else 'en']


# Short, badge-style labels matching the Leaves page pill wording.
# Keep the "<entity> · <state>" pattern so the user instantly knows
# which domain the event is about.
_LABELS = {
    # Leaves
    'leave.submitted':  {'en': 'Leave · submitted',             'ar': 'إجازة · قيد الاعتماد'},
    'leave.validate1':  {'en': 'Leave · pending 2nd approval',  'ar': 'إجازة · بانتظار الاعتماد'},
    'leave.approved':   {'en': 'Leave · approved',              'ar': 'إجازة · معتمدة'},
    'leave.refused':    {'en': 'Leave · refused',               'ar': 'إجازة · مرفوضة'},
    'leave.cancelled':  {'en': 'Leave · cancelled',             'ar': 'إجازة · ملغاة'},
    # Payslips
    'payslip.new':      {'en': 'Payslip · new',                 'ar': 'مسير · جديد'},
    'payslip.paid':     {'en': 'Payslip · paid',                'ar': 'مسير · مصروف'},
    # Expenses
    'expense.submitted':{'en': 'Expense · submitted',           'ar': 'نفقة · جديدة'},
    'expense.approved': {'en': 'Expense · approved',            'ar': 'نفقة · معتمدة'},
    'expense.done':     {'en': 'Expense · reimbursed',          'ar': 'نفقة · مسددة'},
    'expense.refused':  {'en': 'Expense · refused',             'ar': 'نفقة · مرفوضة'},
    # Body fragments
    'fragment.leave_generic': {'en': 'Leave', 'ar': 'إجازة'},
}


def _fmt_days(days, context_lang):
    ar = context_lang and context_lang.lower().startswith('ar')
    n = round(days, 1)
    # Strip trailing .0 for integer-ish counts so it reads "1 يوم"
    # instead of "1.0 يوم".
    if n == int(n):
        n = int(n)
    return f"{n} يوم" if ar else f"{n} day(s)"


class YusrNotificationsController(http.Controller):

    @http.route(
        '/api/yusr/notifications',
        type='http', auth='none', methods=['GET'], csrf=False
    )
    @yusr_authenticated
    def list_notifications(self, employee=None, **kwargs):
        try:
            limit = int(request.httprequest.args.get('limit') or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 100))

        lang = request.env.context.get('lang') or ''

        now = fields.Datetime.now()
        lookback = now - timedelta(days=_LOOKBACK_DAYS)
        unread_cutoff = now - timedelta(hours=_UNREAD_WINDOW_HOURS)

        events = []
        events.extend(self._leave_events(employee, lookback, lang))
        events.extend(self._payslip_events(employee, lookback, lang))
        events.extend(self._expense_events(employee, lookback, lang))

        events.sort(key=lambda e: e['_ts'] or lookback, reverse=True)
        events = events[:limit]

        notifications = [{
            'id': e['id'],
            'title': e['title'],
            'body': e['body'],
            'time': e['_ts'],
            'type': e['type'],
            'unread': bool(e['_ts'] and e['_ts'] >= unread_cutoff),
        } for e in events]

        return ok({'notifications': notifications})

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------
    def _leave_events(self, employee, lookback, lang):
        # with_user(SUPERUSER_ID) for the same reason list_requests in
        # leaves.py does: hr.leave.name is computed with
        # compute_sudo=False and calls env.user.has_group(...), which
        # 500s under auth='none' + bare sudo().
        leaves = request.env['hr.leave'].with_user(SUPERUSER_ID).search([
            ('employee_id', '=', employee.id),
            ('write_date', '>=', lookback),
        ], order='write_date desc', limit=50)
        out = []
        for l in leaves:
            title_key, kind = self._leave_title_key(l.state)
            if not title_key:
                continue
            type_name = l.holiday_status_id.name or _t('fragment.leave_generic', lang)
            date_from = l.request_date_from
            date_to = l.request_date_to
            if date_from and date_to and date_from != date_to:
                span = f"{date_from} → {date_to}"
            elif date_from:
                span = str(date_from)
            else:
                span = ""
            days = l.number_of_days or 0
            body_bits = [type_name]
            if span:
                body_bits.append(span)
            if days:
                body_bits.append(_fmt_days(days, lang))
            out.append({
                'id': f"leave-{l.id}-{kind}",
                'title': _t(title_key, lang),
                'body': " · ".join(body_bits),
                'type': 'leave',
                '_ts': l.write_date,
            })
        return out

    @staticmethod
    def _leave_title_key(state):
        """Map hr.leave.state → (label key, id kind). None pair for
        states we don't surface."""
        return {
            'confirm':   ('leave.submitted',  'submitted'),
            'validate':  ('leave.approved',   'approved'),
            'validate1': ('leave.validate1',  'validate1'),
            'refuse':    ('leave.refused',    'refused'),
            'cancel':    ('leave.cancelled',  'cancelled'),
        }.get(state, (None, None))

    def _payslip_events(self, employee, lookback, lang):
        if 'hr.payslip' not in request.env:
            return []
        payslips = request.env['hr.payslip'].sudo().search([
            ('employee_id', '=', employee.id),
            ('state', 'in', ('done', 'paid')),
            ('write_date', '>=', lookback),
        ], order='write_date desc', limit=20)
        out = []
        for p in payslips:
            key = 'payslip.paid' if p.state == 'paid' else 'payslip.new'
            body_bits = []
            if p.date_from and p.date_to:
                body_bits.append(f"{p.date_from} → {p.date_to}")
            net = getattr(p, 'net_wage', None)
            if net:
                currency_rec = getattr(p, 'currency_id', False) or (
                    p.company_id.currency_id if p.company_id else False
                )
                currency = currency_rec.name if currency_rec else ''
                body_bits.append(f"{round(net, 2)} {currency}".strip())
            out.append({
                'id': f"payslip-{p.id}-{p.state}",
                'title': _t(key, lang),
                'body': " · ".join(body_bits) or (p.number or ""),
                'type': 'message',
                '_ts': p.write_date,
            })
        return out

    def _expense_events(self, employee, lookback, lang):
        expenses = request.env['hr.expense'].sudo().search([
            ('employee_id', '=', employee.id),
            ('write_date', '>=', lookback),
        ], order='write_date desc', limit=30)
        out = []
        for e in expenses:
            key, kind = self._expense_title_key(e.state)
            if not key:
                continue
            currency = e.currency_id.name if e.currency_id else ''
            body = f"{e.name} · {round(e.total_amount, 2)} {currency}".strip()
            out.append({
                'id': f"expense-{e.id}-{kind}",
                'title': _t(key, lang),
                'body': body,
                'type': 'expense',
                '_ts': e.write_date,
            })
        return out

    @staticmethod
    def _expense_title_key(state):
        return {
            'reported': ('expense.submitted', 'submitted'),
            'approved': ('expense.approved',  'approved'),
            'done':     ('expense.done',      'done'),
            'refused':  ('expense.refused',   'refused'),
        }.get(state, (None, None))

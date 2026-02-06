# -*- coding: utf-8 -*-
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    _GOSI_STATUS_SELECTION = [
        ('not_requested', 'Not requested'),
        ('success', 'Last fetch succeeded'),
        ('error', 'Last fetch failed'),
    ]
    _GOSI_SUMMARY_FIELDS = [
        'gosi_response_status_code',
        'gosi_contributor_full_name_en',
        'gosi_contributor_full_name_ar',
        'gosi_contributor_identity_iqama',
        'gosi_contributor_identity_border_number',
        'gosi_engagement_registration_number',
        'gosi_engagement_start_date',
        'gosi_engagement_end_date',
        'gosi_engagement_status',
        'gosi_engagement_type',
        'gosi_engagement_coverage',
        'gosi_engagement_employer_deduction_rate',
        'gosi_engagement_employee_deduction_rate',
    ]

    gosi_contributor_status = fields.Selection(
        selection=_GOSI_STATUS_SELECTION,
        string='Status',
        readonly=True,
        default='not_requested',
        copy=False,
    )
    gosi_contributor_last_checked = fields.Datetime(
        string='Last Check',
        readonly=True,
        copy=False,
    )
    gosi_contributor_last_response = fields.Text(
        string='Last Response',
        readonly=True,
        copy=False,
    )
    gosi_contributor_last_error = fields.Text(
        string='Last Error',
        readonly=True,
        copy=False,
    )
    gosi_response_status_code = fields.Integer(
        string='Status Code',
        readonly=True,
        copy=False,
    )
    gosi_contributor_full_name_en = fields.Char(
        string='Full Name (English)',
        readonly=True,
        copy=False,
    )
    gosi_contributor_full_name_ar = fields.Char(
        string='Full Name (Arabic)',
        readonly=True,
        copy=False,
    )
    gosi_contributor_identity_iqama = fields.Char(
        string='IQAMA Number',
        readonly=True,
        copy=False,
    )
    gosi_contributor_identity_border_number = fields.Char(
        string='Border Number',
        readonly=True,
        copy=False,
    )
    gosi_engagement_registration_number = fields.Char(
        string='GOSI Number',
        readonly=True,
        copy=False,
    )
    gosi_engagement_start_date = fields.Char(
        string='Start Date',
        readonly=True,
        copy=False,
    )
    gosi_engagement_end_date = fields.Char(
        string='End Date',
        readonly=True,
        copy=False,
    )
    gosi_engagement_status = fields.Char(
        string='Engagement Status',
        readonly=True,
        copy=False,
    )
    gosi_engagement_type = fields.Char(
        string='Engagement Type',
        readonly=True,
        copy=False,
    )
    gosi_engagement_coverage = fields.Text(
        string='Coverage Summary',
        readonly=True,
        copy=False,
    )
    gosi_engagement_employer_deduction_rate = fields.Float(
        string='GOSI Employer Deduction Rate',
        readonly=True,
        copy=False,
    )
    gosi_engagement_employee_deduction_rate = fields.Float(
        string='GOSI Employee Deduction Rate',
        readonly=True,
        copy=False,
    )

    def action_gosi_fetch_contributor_deductions(self):
        """Fetch the contributor deductions for each employee and store the raw response."""
        client = self.env['gosi.client'].sudo()
        for employee in self:
            employee._fetch_gosi_contributor_deductions(client)
        return True

    def _fetch_gosi_contributor_deductions(self, client):
        contributor_id = self._determine_contributor_id()
        if not contributor_id:
            raise UserError(_('Provide an Identification Number on the employee form.'))

        try:
            response = client.get_contributor_deductions(contributor_id=contributor_id)
        except UserError as exc:
            self._update_gosi_error_state(str(exc))
            raise
        except Exception as exc:
            _logger.exception('Failed to fetch GOSI contributor deductions for %s', self.display_name)
            message = _('Unable to fetch contributor deductions: %s') % exc
            self._update_gosi_error_state(message)
            raise UserError(message) from exc

        self._update_gosi_success_state(response)

    def _determine_contributor_id(self):
        return (self.identification_id or '').strip()

    def _serialize_gosi_response(self, data):
        if data is None:
            return ''
        if isinstance(data, str):
            return data
        try:
            return json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(data)

    def _update_gosi_success_state(self, response):
        summary = self._summarize_gosi_response(response)
        serialized_response = self._serialize_gosi_response(response)
        
        # Log the response content
        _logger.info('GOSI response for employee %s (ID: %s):\n%s',
                     self.display_name, self.id, serialized_response)
        
        values = {
            'gosi_contributor_status': 'success',
            'gosi_contributor_last_checked': fields.Datetime.now(),
            'gosi_contributor_last_response': serialized_response,
            'gosi_contributor_last_error': False,
        }
        values.update(summary)
        self.write(values)

    def _update_gosi_error_state(self, message):
        self.write({
            'gosi_contributor_status': 'error',
            'gosi_contributor_last_checked': fields.Datetime.now(),
            'gosi_contributor_last_error': message,
            **self._clear_gosi_summary_values(),
        })

    def _clear_gosi_summary_values(self):
        return dict.fromkeys(self._GOSI_SUMMARY_FIELDS, False)

    def _summarize_gosi_response(self, response):
        if not isinstance(response, dict):
            return {}
        full_name = response.get('fullName') or {}
        identity = response.get('identity') or {}
        engagement = response.get('engagements') or {}
        coverage = engagement.get('coverage') or []

        coverage_summary, first_employer, first_employee = self._describe_coverage(coverage)
        return {
            'gosi_response_status_code': response.get('status_code') or response.get('statusCode'),
            'gosi_contributor_full_name_en': (full_name.get('english') or '').strip(),
            'gosi_contributor_full_name_ar': (full_name.get('arabic') or '').strip(),
            'gosi_contributor_identity_iqama': self._as_str(identity.get('iqama')),
            'gosi_contributor_identity_border_number': self._as_str(identity.get('borderNumber')),
            'gosi_engagement_registration_number': self._as_str(engagement.get('registrationNumber')),
            'gosi_engagement_start_date': self._format_date(engagement.get('startDate')),
            'gosi_engagement_end_date': self._format_date(engagement.get('endDate')),
            'gosi_engagement_status': (engagement.get('status') or '').strip(),
            'gosi_engagement_type': (engagement.get('type') or '').strip(),
            'gosi_engagement_coverage': coverage_summary,
            'gosi_engagement_employer_deduction_rate': self._safe_float(first_employer),
            'gosi_engagement_employee_deduction_rate': self._safe_float(first_employee),
        }

    def _describe_coverage(self, coverage_list):
        if not coverage_list:
            return '', None, None
        summary_lines = []
        first_employer = None
        first_employee = None
        for idx, coverage in enumerate(coverage_list):
            name_data = coverage.get('name') if isinstance(coverage, dict) else {}
            label = (name_data.get('english') or name_data.get('arabic') or '').strip()
            if not label:
                label = _('Coverage %s') % (idx + 1)
            employer_rate = coverage.get('employerDeductionRate')
            employee_rate = coverage.get('employeeDeductionRate')
            rates = []
            if employer_rate is not None:
                rates.append(_('Employer %s%%') % employer_rate)
                if idx == 0:
                    first_employer = employer_rate
            if employee_rate is not None:
                rates.append(_('Employee %s%%') % employee_rate)
                if idx == 0:
                    first_employee = employee_rate
            line = f"{label}: {'/'.join(rates)}" if rates else label
            summary_lines.append(line)
        return '; '.join(summary_lines), first_employer, first_employee

    def _as_str(self, value):
        if value in (None, False):
            return ''
        return str(value)

    def _safe_float(self, value):
        try:
            if value is None or value is False:
                return False
            return float(value)
        except (TypeError, ValueError):
            return False

    def _format_date(self, value):
        if not value:
            return False
        try:
            dt = fields.Datetime.from_string(value)
            return dt.date().isoformat()
        except (TypeError, ValueError):
            try:
                return str(value).split('T')[0]
            except Exception:
                return value

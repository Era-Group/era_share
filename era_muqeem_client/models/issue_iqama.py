import json
from datetime import datetime

from odoo import _, api, fields, models


class IssueIqama(models.TransientModel):
    _name = "issue.iqama.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident")
    iqamaNumber = fields.Char(related="employee_id.identification_id", string="Iqama Number", readonly=True, required=True)
    iqamaDuration = fields.Selection(
        [('3', '3'), ('6', '6'), ('9', '9'), ('12', '12'), ('15', '15'), ('18', '18'), ('21', '21'), ('24', '24')],
        string='IqamaDuration',
        required=True,
    )
    json_data = fields.Char(string='JSON Data', compute='_compute_json_data')

    def convert_to_json(self):
        for record in self:
            data = {
                'iqamaNumber': record.iqamaNumber,
                'iqamaDuration': record.iqamaDuration,
            }
            return json.dumps(data)
        return json.dumps({})

    @api.depends('iqamaNumber', 'iqamaDuration')
    def _compute_json_data(self):
        for record in self:
            record.json_data = record.convert_to_json()

    def renew_iqama(self):
        json_data = self.json_data
        url_muqeem = '11'
        company = self.env.company
        user_name, user_password = company._get_api_credentials_client()
        response_data = company.era_call_muqeem(json.loads(json_data), url_muqeem, user_name, user_password)

        vals = {
            'name': _('Issue Iqama'),
            'user': self.env.user.name,
            'employee': self.employee_id.name,
            'date': datetime.now(),
        }
        record = self.env['client.requests'].create(vals)

        if isinstance(response_data, dict):
            status_code = response_data.get('statusCode')

            if status_code == 200:
                datas_main = response_data.get('response_data') or {}
                if isinstance(datas_main, list):
                    datas_main = datas_main[0] if datas_main else {}

                report_data = {
                    'residentName': datas_main.get('residentName'),
                    'translatedResidentName': datas_main.get('translatedResidentName') or False,
                    'iqamaNumber': datas_main.get('iqamaNumber'),
                    'versionNumber': datas_main.get('versionNumber'),
                    'newIqamaExpiryDateHij': datas_main.get('newIqamaExpiryDateHij'),
                    'newIqamaExpiryDateGre': datas_main.get('newIqamaExpiryDateGre'),
                }

                if report_data['newIqamaExpiryDateGre']:
                    self.employee_id.expriry_date_iqama = report_data['newIqamaExpiryDateGre']

                self.employee_id.message_post(body=_('Issue Iqama'))
                record.update({'des': _('Success')})

                data_return = {
                    'form': self.read()[0],
                    'data': [report_data],
                }
                return self.env.ref("era_muqeem_client.renew_iqama_report_id").report_action(self, data=data_return)

            if status_code in (500, 429, 401):
                record.update({'des': _('Fail')})
                return company.show_popup(_('Error'), response_data.get('message'))

            if status_code == 400:
                record.update({'des': _('Fail')})
                if response_data.get('fieldErrors'):
                    error_messages = []
                    for error in response_data.get('fieldErrors'):
                        field = error.get('field')
                        message = error.get('message')
                        error_messages.append(f"{field}: {message}")
                    return company.show_popup(_('Error'), '\n'.join(error_messages))
                return company.show_popup(_('Error'), response_data.get('message'))

        record.update({'des': _('Fail')})
        return company.show_popup(_('Error'), _('Unexpected response from Muqeem service'))

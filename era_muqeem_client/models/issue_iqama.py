import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError




class IssueIqama(models.TransientModel):
    _name = "issue.iqama.wizard"

    employee_id = fields.Many2one('hr.employee', string="Resident")
    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True,required=True)
    iqamaDuration = fields.Selection([('3', '3'), ('6', '6'), ('9', '9'), ('12', '12'), ('15', '15'), ('18', '18'), ('21', '21'), ('24', '24')], string='IqamaDuration',required=True)


    def get_token(self):
        settings = self.env['ir.config_parameter']
        mg_hostname = settings.sudo().get_param('era_muqeem_client.url')
        print("mg_hostname",mg_hostname)

        mg_user_name = settings.sudo().get_param('era_muqeem_client.user_name')

        mg_user_password = settings.sudo().get_param('era_muqeem_client.user_pass')
        if mg_user_name == False:
            raise ValidationError(_('Configuration Missed: User Name'))

        if mg_user_password == False:
            raise ValidationError(_('Configuration Missed: User Password'))


        mg_user_app_id = '13e43c0d'
        mg_user_app_key = '1ed8919dc9370c11b942f19083dab09c'
        mg_user_X_INTEGRATOR_ID = '80c1c102-b9f5-4273-aee3-9e4ca55333a2'



        url = mg_hostname+"/api/authenticate"

        payload = json.dumps({
            "username": mg_user_name,
            "password": mg_user_password
        })
        headers = {
            'app-id': mg_user_app_id,
            'app-key': mg_user_app_key,
            'X-INTEGRATOR-ID': mg_user_X_INTEGRATOR_ID,
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, headers=headers, data=payload, timeout=60)
            response.raise_for_status()
            token = response.json().get('id_token')
            if not token:
                raise ValidationError(_("Failed to retrieve token"))
            return token
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))


    def renew_iqama(self):
        settings = self.env['ir.config_parameter']

        # mg_user_app_id = settings.sudo().get_param('era_muqeem_client.user_app_id')
        # print("mg_user_app_id",mg_user_app_id)
        #
        # mg_user_app_key = settings.sudo().get_param('era_muqeem_client.user_app_key')
        # print("mg_user_app_key",mg_user_app_key)
        #
        # mg_user_X_INTEGRATOR_ID = settings.sudo().get_param('era_muqeem_client.user_X_INTEGRATOR_ID')

        mg_user_app_id = '13e43c0d'
        mg_user_app_key = '1ed8919dc9370c11b942f19083dab09c'
        mg_user_X_INTEGRATOR_ID = '80c1c102-b9f5-4273-aee3-9e4ca55333a2'

        mg_hostname = settings.sudo().get_param('era_muqeem_client.url')
        print("mg_hostname",mg_hostname)

        if mg_hostname == False:
            raise ValidationError(_('Configuration Missed: URl'))
        if mg_user_app_id == False:
            raise ValidationError(_('Configuration Missed: AppId'))

        if mg_user_app_key == False:
            raise ValidationError(_('Configuration Missed: AppKey'))

        if mg_user_X_INTEGRATOR_ID == False:
            raise ValidationError(_('Configuration Missed: X INTEGRATOR ID'))

        url = mg_hostname+"/api/v1/iqama/issue"

        headers = {
            'app-id': mg_user_app_id,
            'app-key':mg_user_app_key,
            'Authorization': f'Bearer {self.get_token()}',
            'X-INTEGRATOR-ID': mg_user_X_INTEGRATOR_ID,
            'Content-Type': 'application/json'
        }

        payload = {
                "iqamaNumber": self.iqamaNumber,
                "iqamaDuration": self.iqamaDuration,
            }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)

            response_data = response.json()
            print('response_data',response_data)
            user_lang = self.env.user.lang
            print('user_lang',user_lang)
            print('###response_data',response_data)
            if response_data.get("message", {}).get("en") == "Error in input data ":
                report_data={
                    "ar": "خطأ في البيانات المدخلة",
                    "en": "Error in input data ",
                    'user_lang':user_lang
                }
            else:


                name = "Attachment Muqeem"
                message = (_('Issue Iqama'))
                employee = self.employee_id
                employee.message_post(
                    body=message,
                )

                report_data = {
                    'residentName': response_data['residentName'],
                    'translatedResidentName': response_data['translatedResidentName'] if response_data.get(
                        'translatedResidentName') else False,
                    'iqamaNumber': response_data['iqamaNumber'],
                    'versionNumber': response_data['versionNumber'],
                    'newIqamaExpiryDateHij': response_data['newIqamaExpiryDateHij'],
                    'newIqamaExpiryDateGre': response_data['newIqamaExpiryDateGre'],
                }

            data_return = {
                'form': self.read()[0],
                'data': [report_data],
            }

            return self.env.ref("era_muqeem_client.renew_iqama_report_id").report_action(self, data=data_return)
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))

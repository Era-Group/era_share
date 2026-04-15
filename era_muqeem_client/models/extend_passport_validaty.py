import json
import base64
from base64 import b64decode
import requests
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError, UserError




class ExtendPassValid(models.TransientModel):
    _name = "extend.passport.validaty"

    employee_id = fields.Many2one('hr.employee', string="Resident",readonly=True)
    iqamaNumber = fields.Char(related="employee_id.iqamaNumber", string="Iqama Number", readonly=True,required=True)
    type_update=fields.Selection([('extend','Extend'),('renew','Renew')] ,string="TypeUpdate",required=True)
    newPassportExpiryDate = fields.Date( string="NewPassportExpiry")
    newPassportIssueDate = fields.Date( string="NewPassportIssueDate")
    newPassportExpiryDate2 = fields.Date( string="newPassportExpiryDate")
    passportNumber = fields.Char( related="employee_id.passportNumber",string='CurrentPassportNumber')
    newPassportNumber = fields.Char( string='NewPassportNumber')
    # location_city_code = fields.Selection(
    #     string='Location City',
    #     selection='_get_location_city_codes'
    # )


    def get_token(self):
        settings = self.env['ir.config_parameter']
        mg_hostname = settings.sudo().get_param('era_muqeem_client.url')
        print("mg_hostname",mg_hostname)

        mg_user_name = settings.sudo().get_param('era_muqeem_client.user_name')

        mg_user_password = settings.sudo().get_param('era_muqeem_client.user_pass')



        mg_user_app_id = '13e43c0d'
        mg_user_app_key = '1ed8919dc9370c11b942f19083dab09c'
        mg_user_X_INTEGRATOR_ID = '80c1c102-b9f5-4273-aee3-9e4ca55333a2'

        url = mg_hostname+"/api/authenticate"

        payload = json.dumps({
            "username": mg_user_name,
            "password": mg_user_password
        })
        print('payload',payload)
        headers = {
            'app-id': mg_user_app_id,
            'app-key': mg_user_app_key,
            'X-INTEGRATOR-ID': mg_user_X_INTEGRATOR_ID,
            'Content-Type': 'application/json'
        }
        print('headers',headers)

        try:
            response = requests.post(url, headers=headers, data=payload, timeout=60)  # Set timeout to 20 seconds
            response.raise_for_status()
            token = response.json().get('id_token')
            if not token:
                raise ValidationError(_("Failed to retrieve token"))
            return token
        except requests.exceptions.Timeout:
            raise UserError(_("The request timed out. Please try again later."))
        except requests.exceptions.RequestException as e:
            raise UserError(_("An error occurred while connecting to the API: %s") % str(e))

    def extend_passport(self):
        if self. type_update ==  'extend' :
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

            url = mg_hostname+"/api/v1/update-information/extend"

            headers = {
                'app-id': mg_user_app_id,
                'app-key':mg_user_app_key,
                'Authorization': f'Bearer {self.get_token()}',
                'Authorization': f'Bearer {self.get_token()}',
                'X-INTEGRATOR-ID': mg_user_X_INTEGRATOR_ID,
                'Content-Type': 'application/json'
            }
            print('headersextend',headers)
            print('newPassportExpiryDate',self.newPassportExpiryDate)
            format_date= self.newPassportExpiryDate.strftime('%Y-%m-%d')
            payload = {
                    "iqamaNumber": self.iqamaNumber,
                    "newPassportExpiryDate": format_date,
                    "passportNumber": self.passportNumber,
                }

            response = requests.post(url, headers=headers, json=payload)

            response_data = response.json()
            user_lang = self.env.user.lang

            if response.status_code == 200:
                self. employee_id.expriry_pass_date=self.newPassportExpiryDate
                name = "Attachment Muqeem"
                message = (_('Extend Passport '))
                employee = self.employee_id
                employee.message_post(
                    body=message,
                )

                report_data = {
                    'message':'The passport validity has been successfully extended',


                }
            elif response_data.get("message", {}).get("en") == "Error in input data ":
                report_data={
                    "ar": "خطأ في البيانات المدخلة",
                    "en": "Error in input data ",
                    'user_lang':user_lang
                }



            data_return = {
                'form': self.read()[0],
                'data': [report_data],
            }

            return self.env.ref("era_muqeem_client.extend_passport_report_id").report_action(self, data=data_return)
        else:
            settings = self.env['ir.config_parameter']
            mg_user_app_id = settings.sudo().get_param('era_muqeem_client.user_app_id')
            mg_user_app_key = settings.sudo().get_param('era_muqeem_client.user_app_key')
            mg_user_X_INTEGRATOR_ID = settings.sudo().get_param('era_muqeem_client.user_X_INTEGRATOR_ID')

            mg_hostname = settings.sudo().get_param('era_muqeem_client.url')
            if mg_hostname == False:
                raise ValidationError(_('You should enter Url'))
            if mg_user_app_id == False:
                raise ValidationError(_('You should enter User App id'))
            if mg_user_app_key == False:
                raise ValidationError(_('You should enter User App key'))
            if mg_user_X_INTEGRATOR_ID == False:
                raise ValidationError(_('You should enter X_INTEGRATOR_ID'))

            url = mg_hostname + "/api/lookups/cities"

            headers = {
                'app-id': mg_user_app_id,
                'app-key': mg_user_app_key,
                'Authorization': f'Bearer {self.get_token()}',
                'X-INTEGRATOR-ID': mg_user_X_INTEGRATOR_ID,
                'Content-Type': 'application/json'
            }
            print('headers',headers)

            response = requests.post(url, headers=headers, )

            print(response.status_code)
            print(response.text)
            user_lang = self.env.user.lang

            # if response.status_code == 200:
            #     print("okkkkkkkk")
            #     print('response.json()',response.json())
            #     report_data = {
            #         'message': 'The passport validity has been successfully extended',
            #
            #     }
            #
            # data_return = {
            #     'form': self.read()[0],
            #     'data': [report_data],
            # }
            #
            # return self.env.ref("era_muqeem_client.extend_passport_report_id").report_action(self,
            #                                                                                        data=data_return)

            # def _get_location_city_codes(self):
            #     # This is where you would normally fetch your list of codes
            #     list_code = ['1', '2', '3']
            #     # Convert the list of codes to the format required for the selection field
            #     return [(code, code) for code in list_code]

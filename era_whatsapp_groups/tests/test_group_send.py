# Part of Era Group custom addons.
from unittest.mock import patch

from odoo.addons.whatsapp.tools.whatsapp_exception import WhatsAppError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWagGroupSend(TransactionCase):
    """Outbound path. No network: the transport is stubbed at its one chokepoint."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.notified = new_test_user(cls.env, login='wag_notified',
                                     name='WAG Notified')
        cls.account = cls.env['whatsapp.account'].sudo().create({
            'name': 'Cloud Account',
            'account_uid': 'waba-1',
            'phone_uid': 'phone-1',
            'app_uid': 'app-1',
            'app_secret': 's',
            'token': 't',
            'notify_user_ids': cls.notified.ids,
        })
        cls.group = cls.env['whatsapp.cloud.group'].sudo().create({
            'account_id': cls.account.id,
            'group_uid': '1234567890@g.us',
            'subject': 'Support Group',
            'enabled': True,
        })

    def _make_message(self, group=None, number=False):
        mail_message = self.env['mail.message'].sudo().create({
            'model': 'discuss.channel', 'res_id': 1, 'body': 'hello',
            'message_type': 'whatsapp_message',
        })
        return self.env['whatsapp.message'].sudo().create({
            'mail_message_id': mail_message.id,
            'wa_account_id': self.account.id,
            'mobile_number': number or False,
            'wag_group_id': group.id if group else False,
            'state': 'outgoing',
        })

    def test_group_message_needs_no_phone_number(self):
        """The upstream guard raises phone_invalid on an empty number; a group has none."""
        message = self._make_message(group=self.group)
        self.assertEqual(message._assert_recipient_identifier(), '1234567890@g.us')

    def test_non_group_message_still_requires_a_number(self):
        message = self._make_message()
        with self.assertRaises(WhatsAppError):
            message._assert_recipient_identifier()

    def test_send_routes_to_the_group_endpoint_with_recipient_type_group(self):
        message = self._make_message(group=self.group)
        captured = {}

        def _fake_request(self_api, method, url, auth_type='', params=False,
                          headers=None, data=False, files=False, endpoint_include=False):
            captured['url'] = url
            captured['data'] = data

            class _Resp:
                status_code = 200

                @staticmethod
                def json():
                    return {'messages': [{'id': 'wamid.GROUP'}]}
            return _Resp()

        with patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._check_allow_requests'), \
             patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._WhatsAppApi__api_requests',
                   _fake_request):
            result = message._send_with_identifier(
                None, message_type='text', send_vals={'body': 'hi'})

        self.assertEqual(result['msg_uid'], 'wamid.GROUP')
        self.assertIn('/phone-1/messages', captured['url'])
        self.assertIn('"recipient_type": "group"', captured['data'])
        self.assertIn('"to": "1234567890@g.us"', captured['data'])

    def test_a_message_without_a_group_is_left_to_super(self):
        """The override must be inert for ordinary 1:1 traffic."""
        message = self._make_message(number='+966500000000')
        calls = []

        def _fake_send(self_api, bsuid=None, number=None, **kwargs):
            calls.append(number)
            return {'msg_uid': 'wamid.DIRECT', 'wa_id': None}

        with patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._check_allow_requests'), \
             patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._send_whatsapp_to_identifier',
                   _fake_send):
            from odoo.addons.whatsapp.tools.whatsapp_api import WhatsAppApi
            result = message._send_with_identifier(
                WhatsAppApi(self.account), message_type='text', send_vals={'body': 'hi'})

        self.assertEqual(result['msg_uid'], 'wamid.DIRECT')
        self.assertEqual(calls, [message.mobile_number_formatted])

    def test_unsupported_message_type_fails_locally(self):
        """Upstream silently builds a contentless payload; we refuse before the wire."""
        from odoo.addons.era_whatsapp_groups.tools.whatsapp_group_api import WhatsAppGroupApi
        with patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._check_allow_requests'):
            api = WhatsAppGroupApi(self.account)
            with self.assertRaises(WhatsAppError):
                api._wag_send_to_group('g@g.us', 'location', {'x': 1})

    def test_group_creation_refuses_more_than_eight_participants(self):
        from odoo.addons.era_whatsapp_groups.tools.whatsapp_group_api import WhatsAppGroupApi
        with patch('odoo.addons.whatsapp.tools.whatsapp_api.WhatsAppApi._check_allow_requests'):
            api = WhatsAppGroupApi(self.account)
            with self.assertRaises(WhatsAppError):
                api._wag_create_group('Too big', participants=[str(i) for i in range(9)])

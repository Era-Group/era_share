# Part of Era Group custom addons.
"""The regression net for running alongside era_waha_integration.

Both modules patch whatsapp.account, whatsapp.message and discuss.channel. These
tests exist because the failure mode is silent: a WAHA message routed through the
Cloud API transport does not raise, it sends to the wrong place.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWagCoexistence(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.notified = new_test_user(cls.env, login='wag_notified',
                                     name='WAG Notified')
        cls.cloud = cls.env['whatsapp.account'].sudo().create({
            'name': 'Cloud Account',
            'account_uid': 'waba-3', 'phone_uid': 'phone-3',
            'app_uid': 'app-3', 'app_secret': 's', 'token': 't',
            'notify_user_ids': cls.notified.ids,
        })
        cls.has_waha = 'provider' in cls.env['whatsapp.account']._fields
        if cls.has_waha:
            cls.cloud.sudo().provider = 'meta'

    def test_module_loads_after_era_waha_integration(self):
        """Our overrides only work if this class is the more derived one.

        Odoo orders modules by (depth, name); both depend on 'whatsapp' (depth 2),
        so 'era_whatsapp_groups' sorts after 'era_waha_integration'. If that ever
        changes, _send_with_identifier stops being reached for Meta accounts and
        this test is the only thing that would say so.
        """
        if not self.has_waha:
            self.skipTest('era_waha_integration is not installed')
        mro_names = [
            cls.__module__ for cls in type(self.env['whatsapp.message']).mro()
            if 'era_' in cls.__module__
        ]
        groups_idx = next(i for i, n in enumerate(mro_names) if 'era_whatsapp_groups' in n)
        waha_idx = next(i for i, n in enumerate(mro_names) if 'era_waha_integration' in n)
        self.assertLess(groups_idx, waha_idx,
                        "era_whatsapp_groups must be more derived than era_waha_integration")

    def test_cloud_guard_is_true_without_era_waha_integration(self):
        """Standalone install: there is no 'provider' field, so every account is Cloud."""
        from odoo.addons.era_whatsapp_groups.models.whatsapp_account import wag_is_cloud_account
        self.assertTrue(wag_is_cloud_account(self.cloud))

    def test_a_waha_account_never_reaches_cloud_group_code(self):
        if not self.has_waha:
            self.skipTest('era_waha_integration is not installed')
        from odoo.addons.era_whatsapp_groups.models.whatsapp_account import wag_is_cloud_account
        waha = self.env['whatsapp.account'].sudo().create({
            'name': 'WAHA Account', 'provider': 'waha',
            'account_uid': 'waba-4', 'phone_uid': 'phone-4',
            'app_uid': 'app-4', 'app_secret': 's', 'token': 't',
            'notify_user_ids': self.notified.ids,
            # era_waha_integration._check_provider_fields requires both.
            'waha_server_url': 'http://waha.test', 'waha_session': 'sess-4',
        })
        self.assertFalse(wag_is_cloud_account(waha))
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            waha._wag_api()

    def test_a_waha_group_channel_is_not_selected_by_our_filters(self):
        """WAHA group channels carry waha_chat_id and no wag_group_uid, so a search
        on ours can never pick one up -- and vice versa."""
        if not self.has_waha:
            self.skipTest('era_waha_integration is not installed')
        waha = self.env['whatsapp.account'].sudo().create({
            'name': 'WAHA Account 2', 'provider': 'waha',
            'account_uid': 'waba-5', 'phone_uid': 'phone-5',
            'app_uid': 'app-5', 'app_secret': 's', 'token': 't',
            'notify_user_ids': self.notified.ids,
            'waha_server_url': 'http://waha.test', 'waha_session': 'sess-5',
        })
        waha_group = self.env['whatsapp.waha.group'].sudo().create({
            'account_id': waha.id, 'chat_id': '99999@g.us',
            'subject': 'WAHA Group', 'enabled': True,
        })
        channel = self.env['discuss.channel'].sudo().create({
            'name': 'WAHA Group', 'channel_type': 'whatsapp',
            'wa_account_id': waha.id,
            'waha_chat_id': waha_group.chat_id,
            'waha_group_id': waha_group.id,
        })
        self.assertFalse(channel.wag_group_id)
        self.assertFalse(self.env['discuss.channel'].sudo().search([
            ('wag_group_uid', '=', '99999@g.us')]))

    def test_the_phone_constraint_survives_both_overrides(self):
        """Both modules override _check_whatsapp_number. If either drops the
        @api.constrains decorator, Odoo silently stops enforcing it entirely."""
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.env['discuss.channel'].sudo().create({
                'name': 'Numberless', 'channel_type': 'whatsapp',
                'wa_account_id': self.cloud.id,
            })

    def test_our_constraint_field_is_in_the_trigger_list(self):
        fields_list = self.env['discuss.channel']._check_whatsapp_number_contains_fields()
        self.assertIn('wag_group_uid', fields_list)
        self.assertIn('channel_type', fields_list)

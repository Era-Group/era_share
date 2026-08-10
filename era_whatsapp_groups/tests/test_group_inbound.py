# Part of Era Group custom addons.
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWagGroupInbound(TransactionCase):
    """Webhook -> channel routing, and the allowlist that gates it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.notified = new_test_user(cls.env, login='wag_notified',
                                     name='WAG Notified')
        cls.account = cls.env['whatsapp.account'].sudo().create({
            'name': 'Cloud Account',
            'account_uid': 'waba-2',
            'phone_uid': 'phone-2',
            'app_uid': 'app-2',
            'app_secret': 's',
            'token': 't',
            'notify_user_ids': cls.notified.ids,
        })
        cls.group = cls.env['whatsapp.cloud.group'].sudo().create({
            'account_id': cls.account.id,
            'group_uid': 'gid-approved',
            'subject': 'Approved Group',
            'enabled': True,
        })

    def _payload(self, group_uid):
        return {'from': '966500000000', 'type': 'text',
                'text': {'body': 'hi'}, 'group_id': group_uid}

    def test_group_id_is_read_from_several_spellings(self):
        """Meta's docs disagree with themselves elsewhere; a silent miss would
        misroute a group message into a 1:1 channel keyed on the sender."""
        for payload in ({'group_id': 'g1'},
                        {'group': 'g1'},
                        {'group': {'id': 'g1'}},
                        {'recipient_group_id': 'g1'}):
            self.assertEqual(self.account._wag_group_uid_from_payload(payload), 'g1')
        self.assertEqual(self.account._wag_group_uid_from_payload({'from': '9665'}), '')

    def test_an_approved_group_gets_a_channel(self):
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        self.assertTrue(channel)
        self.assertEqual(channel.wag_group_id, self.group)
        self.assertEqual(channel.channel_type, 'whatsapp')
        self.assertFalse(channel.whatsapp_number, "a group channel has no single number")
        self.assertEqual(self.group.channel_id, channel)

    def test_the_same_group_reuses_its_channel(self):
        first = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        second = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        self.assertEqual(first, second)

    def test_a_disabled_group_is_dropped_not_channelled(self):
        """Discovery must never imply authorisation: an unapproved group opening a
        channel would pull an outside conversation into the company's record."""
        self.group.enabled = False
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        self.assertFalse(channel)

    def test_an_unknown_group_is_dropped(self):
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-never-seen'), [], create_if_not_found=True)
        self.assertFalse(channel)

    def test_an_unavailable_group_is_dropped(self):
        self.group.sudo().available = False
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        self.assertFalse(channel)

    def test_a_group_channel_is_exempt_from_the_phone_number_constraint(self):
        """And the constraint must still fire for everything else -- overriding it
        without re-declaring @api.constrains deletes it outright."""
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        channel.flush_recordset()
        from odoo.exceptions import ValidationError
        with self.assertRaises(ValidationError, msg="the constraint must survive our override"):
            self.env['discuss.channel'].sudo().create({
                'name': 'No number', 'channel_type': 'whatsapp',
                'wa_account_id': self.account.id,
            })

    def test_group_author_comes_from_the_context(self):
        channel = self.account._find_active_channel_from_whatsapp_message_values(
            self._payload('gid-approved'), [], create_if_not_found=True)
        partner = self.env['res.partner'].sudo().create({'name': 'Group Member'})
        message = channel.sudo().with_context(
            wag_group_author_id=partner.id).message_post(body='from a member')
        self.assertEqual(message.author_id, partner)

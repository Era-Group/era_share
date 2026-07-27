# Part of Era Group custom addons.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWahaNotificationPolicy(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.default_user = new_test_user(
            cls.env, login='waha_default_user', name='WAHA Default User')
        cls.participant_user = new_test_user(
            cls.env, login='waha_participant_user', name='WAHA Participant User')
        cls.account = cls.env['whatsapp.account'].sudo().create({
            'name': 'WAHA Notification Test',
            'provider': 'waha',
            'waha_server_url': 'http://waha.test',
            'waha_session': 'notification-test',
            'notify_user_ids': cls.default_user.ids,
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'WAHA Notification Customer',
            'phone': '+15550000001',
        })
        cls.channel = cls.env['discuss.channel'].sudo().create({
            'name': 'WAHA Notification Channel',
            'channel_type': 'whatsapp',
            'wa_account_id': cls.account.id,
            'whatsapp_partner_id': cls.customer.id,
            'whatsapp_number': '15550000001',
        })
        cls.account._waha_grant_members(cls.channel)
        cls.channel.add_members(
            partner_ids=cls.participant_user.partner_id.ids,
            post_joined_message=False,
        )

    def _recipient_ids(self, message, *, live=True):
        recipients = self.channel._notify_get_recipients(
            message,
            msg_vals={'message_type': 'whatsapp_message', 'author_id': self.customer.id},
            whatsapp_inbound_msg_uid='inbound-test-id',
            **({'waha_live_inbound': True} if live else {}),
        )
        return {recipient['id'] for recipient in recipients}

    def test_inbound_notifies_defaults_until_a_user_participates(self):
        message = self.channel.message_post(
            body='First inbound message',
            message_type='whatsapp_message',
            author_id=self.customer.id,
            subtype_xmlid='mail.mt_comment',
            whatsapp_inbound_msg_uid='inbound-test-first',
            waha_live_inbound=True,
        )
        self.assertEqual(self._recipient_ids(message), {self.default_user.partner_id.id})

        self.channel.waha_participant_user_ids = self.participant_user
        self.assertEqual(self._recipient_ids(message), {self.participant_user.partner_id.id})

    def test_outbound_and_imported_history_are_quiet(self):
        message = self.channel.message_post(
            body='Imported inbound message',
            message_type='whatsapp_message',
            author_id=self.customer.id,
            subtype_xmlid='mail.mt_comment',
            whatsapp_inbound_msg_uid='inbound-test-history',
        )
        self.assertFalse(self._recipient_ids(message, live=False))
        self.assertFalse(self.channel._notify_get_recipients(
            message,
            msg_vals={'message_type': 'whatsapp_message', 'author_id': self.default_user.partner_id.id},
        ))

    def test_internal_note_mention_adds_participant(self):
        self.channel.with_user(self.default_user).message_post(
            body='Please take this conversation.',
            message_type='comment',
            author_id=self.default_user.partner_id.id,
            partner_ids=[self.participant_user.partner_id.id],
            waha_internal_note=True,
        )
        self.assertEqual(
            self.channel.waha_participant_user_ids,
            self.default_user + self.participant_user,
        )

    def test_unanswered_live_inbound_escalates_once(self):
        message = self.channel.message_post(
            body='Unanswered inbound message',
            message_type='whatsapp_message',
            author_id=self.customer.id,
            subtype_xmlid='mail.mt_comment',
            whatsapp_inbound_msg_uid='inbound-test-escalation',
            waha_live_inbound=True,
        )
        self.env.cr.execute(
            "UPDATE mail_message SET create_date = NOW() - INTERVAL '61 minutes' WHERE id = %s",
            [message.id],
        )
        message.invalidate_recordset()
        with patch.object(type(self.account), '_waha_company_is_working', return_value=True):
            self.account._cron_waha_escalate_unanswered()
        self.assertTrue(message.waha_escalated_at)

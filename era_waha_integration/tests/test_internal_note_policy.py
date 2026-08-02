# Part of Era Group custom addons.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWhatsappInternalNotePolicy(TransactionCase):
    """A mention of a colleague, or an explicit internal note, must never be sent
    to the customer — on WAHA channels AND on official (Meta) WhatsApp channels."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The real send path (whatsapp_identifiers) reads group-restricted partner
        # fields, so the sending agent needs the WhatsApp admin group.
        cls.agent = new_test_user(
            cls.env, login='wa_note_agent', name='WA Note Agent',
            groups='base.group_user,whatsapp.group_whatsapp_admin')
        cls.colleague = new_test_user(cls.env, login='wa_note_colleague', name='WA Note Colleague')
        cls.meta_account = cls.env['whatsapp.account'].sudo().create({
            'name': 'Meta Note Test',
            'provider': 'meta',
            'app_uid': 'test-app-uid',
            'account_uid': 'test-account-uid',
            'phone_uid': 'test-phone-uid',
            'token': 'test-token',
            'notify_user_ids': cls.agent.ids,
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Meta Note Customer',
            'phone': '+15550000002',
        })
        cls.channel = cls.env['discuss.channel'].sudo().create({
            'name': 'Meta Note Channel',
            'channel_type': 'whatsapp',
            'wa_account_id': cls.meta_account.id,
            'whatsapp_partner_id': cls.customer.id,
            'whatsapp_number': '15550000002',
        })
        cls.channel.add_members(
            partner_ids=(cls.agent.partner_id | cls.colleague.partner_id).ids,
            post_joined_message=False,
        )

    def test_meta_internal_mention_becomes_note(self):
        message = self.channel.with_user(self.agent).message_post(
            body='@Colleague please handle this one.',
            message_type='whatsapp_message',
            subtype_xmlid='mail.mt_comment',
            partner_ids=[self.colleague.partner_id.id],
        )
        self.assertEqual(message.message_type, 'comment')
        self.assertEqual(message.subtype_id, self.env.ref('mail.mt_note'))
        self.assertFalse(message.wa_message_ids, 'A mention must never create a WhatsApp send')

    def test_meta_internal_note_flag_becomes_note(self):
        message = self.channel.with_user(self.agent).message_post(
            body='Internal remark for the team.',
            message_type='whatsapp_message',
            subtype_xmlid='mail.mt_comment',
            waha_internal_note=True,
        )
        self.assertEqual(message.message_type, 'comment')
        self.assertEqual(message.subtype_id, self.env.ref('mail.mt_note'))
        self.assertFalse(message.wa_message_ids, 'An internal note must never create a WhatsApp send')

    def test_meta_customer_mention_still_sends(self):
        """Mentioning the customer partner (no internal user) is not an internal note."""
        with patch.object(
            type(self.env['whatsapp.message']), '_send_message',
            lambda records, with_commit=False: None,
        ):
            message = self.channel.with_user(self.agent).message_post(
                body='Hello customer',
                message_type='whatsapp_message',
                subtype_xmlid='mail.mt_comment',
                partner_ids=[self.customer.id],
            )
        self.assertEqual(message.message_type, 'whatsapp_message')
        self.assertTrue(message.wa_message_ids, 'A customer mention must still send normally')

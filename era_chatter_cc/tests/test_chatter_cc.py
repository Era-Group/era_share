import ast
import json

from odoo import Command
from odoo.addons.mail.tests.common import MailCommon
from odoo.tests import HttpCase, tagged, users


class ChatterCcCommon(MailCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.record = cls.env['res.partner'].create({
            'name': 'Cc Test Record',
            'email': 'record@test.example.com',
        })
        cls.partner_to = cls.env['res.partner'].create({
            'name': 'Toto To',
            'email': 'toto.to@test.example.com',
        })
        cls.partner_cc = cls.env['res.partner'].create({
            'name': 'Cece Cc',
            'email': 'cece.cc@test.example.com',
        })

    def _post_with_cc(self, cc_partners=None, to_partners=None, subtype_xmlid='mail.mt_comment'):
        cc_partners = self.partner_cc if cc_partners is None else cc_partners
        to_partners = self.partner_to if to_partners is None else to_partners
        return self.record.with_env(self.env).message_post(
            body='<p>Hello</p>',
            message_type='comment',
            subtype_xmlid=subtype_xmlid,
            partner_ids=to_partners.ids,
            partner_cc_ids=[Command.set(cc_partners.ids)],
        )

    def _mail_headers(self, message):
        mails = self.env['mail.mail'].sudo().search([('mail_message_id', '=', message.id)])
        return [ast.literal_eval(mail.headers) if mail.headers else {} for mail in mails]


@tagged('post_install', '-at_install', 'era_chatter_cc')
class TestChatterCc(ChatterCcCommon):

    @users('employee')
    def test_cc_partner_is_notified(self):
        with self.mock_mail_gateway():
            message = self._post_with_cc()
        self.assertEqual(message.partner_cc_ids, self.partner_cc)
        self.assertIn(self.partner_cc, message.partner_ids)
        self.assertIn(self.partner_to, message.partner_ids)
        notification = self.env['mail.notification'].sudo().search([
            ('mail_message_id', '=', message.id),
            ('res_partner_id', '=', self.partner_cc.id),
        ])
        self.assertEqual(notification.notification_type, 'email')

    @users('employee')
    def test_cc_header_on_notification_mails(self):
        with self.mock_mail_gateway():
            message = self._post_with_cc()
        headers_list = self._mail_headers(message)
        self.assertTrue(headers_list, 'The message should have generated notification emails')
        for headers in headers_list:
            self.assertIn('X-Msg-Cc-Add', headers)
            self.assertIn('cece.cc@test.example.com', headers['X-Msg-Cc-Add'])
            self.assertNotIn(
                'cece.cc@test.example.com', headers.get('X-Msg-To-Add', ''),
                'A Cc recipient should not also be advertised on the To line',
            )

    @users('employee')
    def test_cc_header_deduplicated_against_to(self):
        """ Somebody who is both To and Cc is listed once, on the Cc line. """
        with self.mock_mail_gateway():
            message = self._post_with_cc(
                cc_partners=self.partner_cc,
                to_partners=self.partner_to + self.partner_cc,
            )
        for headers in self._mail_headers(message):
            self.assertNotIn('cece.cc@test.example.com', headers.get('X-Msg-To-Add', ''))
            self.assertEqual(headers['X-Msg-Cc-Add'].count('cece.cc@test.example.com'), 1)

    @users('employee')
    def test_cc_header_skipped_above_leak_threshold(self):
        limit = self.env['mail.thread']._CUSTOMER_HEADERS_LIMIT_COUNT
        many_cc = self.env['res.partner'].create([
            {'name': f'Cc {idx}', 'email': f'cc{idx}@test.example.com'}
            for idx in range(limit + 1)
        ])
        with self.mock_mail_gateway():
            message = self._post_with_cc(cc_partners=many_cc)
        for headers in self._mail_headers(message):
            self.assertNotIn('X-Msg-Cc-Add', headers)

    @users('employee')
    def test_note_handled_like_a_message(self):
        """ The UI never offers a Cc for an internal note, but a Cc given
        through the API is honoured the same way it is on a message: a note
        does notify its explicit recipients. """
        with self.mock_mail_gateway():
            message = self._post_with_cc(subtype_xmlid='mail.mt_note')
        self.assertEqual(message.partner_cc_ids, self.partner_cc)
        self.assertIn(self.partner_cc, message.partner_ids)
        for headers in self._mail_headers(message):
            self.assertIn('cece.cc@test.example.com', headers.get('X-Msg-Cc-Add', ''))

    def test_alter_message_promotes_cc_header(self):
        IrMailServer = self.env['ir.mail_server']
        mime = IrMailServer._build_email__(
            email_from='"Sender" <sender@test.example.com>',
            email_to=['"Toto" <toto.to@test.example.com>'],
            subject='Subject',
            body='<p>Body</p>',
            subtype='html',
            headers={'X-Msg-Cc-Add': '"Cece" <cece.cc@test.example.com>'},
        )
        # the envelope recipients are computed before '_alter_message__' runs
        # in '_prepare_email_message', which is what makes the Cc header
        # display-only: it delivers no additional copy.
        smtp_to_list = IrMailServer._prepare_smtp_to_list(mime, None)
        self.assertNotIn('cece.cc@test.example.com', smtp_to_list)

        IrMailServer._alter_message__(mime, 'sender@test.example.com', smtp_to_list)
        self.assertIn('cece.cc@test.example.com', mime['Cc'] or '')
        self.assertIn('toto.to@test.example.com', mime['To'] or '')
        self.assertFalse(mime['X-Msg-Cc-Add'])
        self.assertEqual(
            smtp_to_list, ['toto.to@test.example.com'],
            'The SMTP recipient list is untouched by the Cc header',
        )

    def test_alter_message_deduplicates_against_to(self):
        IrMailServer = self.env['ir.mail_server']
        mime = IrMailServer._build_email__(
            email_from='"Sender" <sender@test.example.com>',
            email_to=['"Toto" <toto.to@test.example.com>'],
            subject='Subject',
            body='<p>Body</p>',
            subtype='html',
            headers={'X-Msg-Cc-Add': '"Toto" <toto.to@test.example.com>'},
        )
        IrMailServer._alter_message__(mime, 'sender@test.example.com', [])
        self.assertFalse(mime['Cc'])
        self.assertFalse(mime['X-Msg-Cc-Add'])

    @users('employee')
    def test_full_composer_carries_cc(self):
        composer = self.env['mail.compose.message'].with_context(
            default_model=self.record._name,
            default_res_ids=self.record.ids,
            default_composition_mode='comment',
        ).create({
            'body': '<p>From the full composer</p>',
            'subject': 'Full composer',
            'partner_ids': [Command.set(self.partner_to.ids)],
            'partner_cc_ids': [Command.set(self.partner_cc.ids)],
        })
        with self.mock_mail_gateway():
            _mails, messages = composer._action_send_mail()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages.partner_cc_ids, self.partner_cc)
        self.assertIn(self.partner_cc, messages.partner_ids)

    @users('employee')
    def test_mass_mail_ignores_cc(self):
        composer = self.env['mail.compose.message'].with_context(
            default_model=self.record._name,
            default_res_ids=self.record.ids,
            default_composition_mode='mass_mail',
        ).create({
            'body': '<p>Mass</p>',
            'subject': 'Mass',
            'partner_cc_ids': [Command.set(self.partner_cc.ids)],
        })
        values = composer._prepare_mail_values(self.record.ids)
        for mail_values in values.values():
            self.assertNotIn('partner_cc_ids', mail_values)

    @users('employee')
    def test_scheduled_message_carries_cc(self):
        composer = self.env['mail.compose.message'].with_context(
            default_model=self.record._name,
            default_res_ids=self.record.ids,
            default_composition_mode='comment',
        ).create({
            'body': '<p>Later</p>',
            'subject': 'Scheduled',
            'partner_ids': [Command.set(self.partner_to.ids)],
            'partner_cc_ids': [Command.set(self.partner_cc.ids)],
            'scheduled_date': '2050-01-01 08:00:00',
        })
        scheduled = composer._action_schedule_message()
        parameters = json.loads(scheduled.notification_parameters or '{}')
        self.assertIn('partner_cc_ids', parameters)
        self.assertIn(self.partner_cc, scheduled.partner_ids)
        with self.mock_mail_gateway():
            scheduled.post_message()
        message = self.env['mail.message'].sudo().search(
            [('model', '=', self.record._name), ('res_id', '=', self.record.id)],
            order='id desc', limit=1,
        )
        self.assertEqual(message.partner_cc_ids, self.partner_cc)


@tagged('post_install', '-at_install', 'era_chatter_cc')
class TestChatterCcController(ChatterCcCommon, HttpCase):

    def test_post_route_resolves_cc_emails(self):
        self.authenticate('employee', 'employee')
        with self.mock_mail_gateway():
            self.make_jsonrpc_request('/mail/message/post', {
                'thread_model': self.record._name,
                'thread_id': self.record.id,
                'post_data': {
                    'body': '<p>Route</p>',
                    'message_type': 'comment',
                    'subtype_xmlid': 'mail.mt_comment',
                    'partner_ids': [self.partner_to.id],
                    'partner_cc_ids': [self.partner_cc.id],
                    'partner_cc_emails': ['new.cc@test.example.com'],
                },
            })
        message = self.env['mail.message'].sudo().search(
            [('model', '=', self.record._name), ('res_id', '=', self.record.id)],
            order='id desc', limit=1,
        )
        created = self.env['res.partner'].sudo().search(
            [('email_normalized', '=', 'new.cc@test.example.com')],
        )
        self.assertEqual(len(created), 1, 'The Cc email should have created a partner')
        self.assertEqual(message.partner_cc_ids, self.partner_cc + created)
        self.assertLessEqual(
            set((self.partner_cc + created).ids), set(message.partner_ids.ids),
            'Cc recipients must also be real recipients so that they get notified',
        )

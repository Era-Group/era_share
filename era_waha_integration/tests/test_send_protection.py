# Part of Era Group custom addons.
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWahaSendProtection(TransactionCase):
    """Hardening added after the 2026-07-28 session loss."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(cls.env, login='waha_agent', name='WAHA Agent')
        cls.other_agent = new_test_user(cls.env, login='waha_agent2', name='WAHA Agent 2')
        cls.account = cls.env['whatsapp.account'].sudo().create({
            'name': 'WAHA Protection Test',
            'provider': 'waha',
            'waha_server_url': 'http://waha.test',
            'waha_session': 'protection-test',
            'notify_user_ids': cls.agent.ids,
            'waha_new_number_daily_limit': 2,
            'waha_new_number_account_daily_limit': 3,
            'waha_check_number_exists': False,
            'waha_send_window_active': False,
        })

    def setUp(self):
        super().setUp()
        # `_waha_update` deliberately commits through its own cursor so rapid session
        # writes never abort a live request. That cursor cannot see records this test
        # has not committed, so run those writes in-transaction instead — the behaviour
        # under test is what gets written, not which cursor writes it.
        def _in_transaction_update(account, vals, event_vals=None):
            if vals:
                account.sudo().write(vals)
            if event_vals:
                self.env['whatsapp.waha.session.event'].sudo().create(
                    dict(event_vals, account_id=account.id))
            return True

        patcher = patch.object(type(self.account), '_waha_update', _in_transaction_update)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_channel(self, number, name='WAHA Contact'):
        partner = self.env['res.partner'].create({'name': name, 'phone': '+' + number})
        return self.env['discuss.channel'].sudo().create({
            'name': name,
            'channel_type': 'whatsapp',
            'wa_account_id': self.account.id,
            'whatsapp_partner_id': partner.id,
            'whatsapp_number': number,
        })

    def _post(self, channel, direction, author, when=None):
        """Create a mail.message + whatsapp.message pair the guards can count."""
        message = self.env['mail.message'].sudo().create({
            'model': 'discuss.channel',
            'res_id': channel.id,
            'body': 'x',
            'message_type': 'whatsapp_message',
            'author_id': author.partner_id.id,
        })
        wa = self.env['whatsapp.message'].sudo().create({
            'mail_message_id': message.id,
            'wa_account_id': self.account.id,
            'message_type': direction,
            'state': 'sent' if direction == 'outbound' else 'received',
            'mobile_number': '+' + (channel.whatsapp_number or ''),
        })
        if when:
            self.env.cr.execute(
                "UPDATE mail_message SET create_date = %s WHERE id = %s", (when, message.id))
            message.invalidate_recordset(['create_date'])
        return wa

    # -- cold-start counter ------------------------------------------------

    def test_cold_start_still_counts_after_the_contact_replies(self):
        """A reply must not erase the first contact from today's tally.

        The previous counter only looked at conversations that were *still* silent, so
        every contact who answered freed up a slot and the cap never engaged.
        """
        channel = self._make_channel('15550001001')
        self._post(channel, 'outbound', self.agent)
        self.assertEqual(self.account._waha_cold_start_count_today(user=self.agent), 1)
        self._post(channel, 'inbound', self.agent)
        self.assertEqual(
            self.account._waha_cold_start_count_today(user=self.agent), 1,
            "the reply arrived after our first contact, so it stays a cold start")

    def test_reply_to_an_existing_conversation_is_not_a_cold_start(self):
        channel = self._make_channel('15550001002')
        yesterday = fields.Datetime.now() - timedelta(days=1)
        self._post(channel, 'inbound', self.agent, when=yesterday)
        self._post(channel, 'outbound', self.agent)
        self.assertEqual(self.account._waha_cold_start_count_today(user=self.agent), 0)

    def test_account_wide_cap_bounds_all_users_together(self):
        for i, user in enumerate([self.agent, self.other_agent, self.agent]):
            self._post(self._make_channel('1555000200%s' % i), 'outbound', user)
        self.assertEqual(self.account._waha_cold_start_count_today(), 3)
        with self.assertRaises(UserError):
            self.account._waha_check_send_allowed(
                '15550002099', self.other_agent, channel=None, check_new=True)

    # -- recipient validity ------------------------------------------------

    def test_landline_is_refused_and_mobile_is_allowed(self):
        self.assertFalse(self.account._waha_number_is_mobile('+966114532924'))
        self.assertTrue(self.account._waha_number_is_mobile('+966582595227'))

    def test_unparseable_number_is_allowed(self):
        """Fail open — a false block is worse than a wasted send."""
        self.assertTrue(self.account._waha_number_is_mobile('not-a-number'))

    def test_check_exists_only_blocks_on_a_definite_no(self):
        self.account.waha_check_number_exists = True
        with patch.object(type(self.account), '_waha_request',
                          return_value={'numberExists': False}):
            self.assertFalse(self.account._waha_number_exists_on_whatsapp('+15550003001'))
        with patch.object(type(self.account), '_waha_request',
                          side_effect=UserError('server down')):
            self.assertTrue(self.account._waha_number_exists_on_whatsapp('+15550003001'))

    # -- circuit breaker ---------------------------------------------------

    def test_breaker_trips_after_repeated_status_changes(self):
        self.account.write({'waha_flap_threshold': 3, 'waha_flap_window_minutes': 60})
        for status in ('starting', 'working', 'failed', 'starting'):
            self.account._waha_write_status(status)
        self.assertTrue(self.account.waha_paused_until,
                        "a reconnect loop must pause outbound sending")
        self.assertTrue(self.account._waha_hold_reason())

    def test_resume_clears_the_pause(self):
        self.account.write({
            'waha_paused_until': fields.Datetime.now() + timedelta(hours=1),
            'waha_pause_reason': 'test',
        })
        self.account.action_waha_resume_sending()
        self.assertFalse(self.account.waha_paused_until)
        self.assertIsNone(self.account._waha_hold_reason())

    def test_status_transitions_are_logged_with_timestamps(self):
        self.account._waha_write_status('working', source='webhook')
        event = self.env['whatsapp.waha.session.event'].search(
            [('account_id', '=', self.account.id)], limit=1)
        self.assertEqual(event.status, 'working')
        self.assertEqual(event.source, 'webhook')

    # -- form rendering ----------------------------------------------------

    def test_account_form_and_field_descriptions_load(self):
        """Installing the module is not enough to prove the form opens.

        A Selection whose source is passed by name has to resolve to an attribute on the
        model; getting that wrong raises only when fields_get runs, i.e. the first time
        somebody opens the account — never during install or the other tests here.
        """
        descriptions = self.env['whatsapp.account'].fields_get()
        self.assertTrue(descriptions['waha_tz']['selection'])
        self.env['whatsapp.account'].get_views(
            [(self.env.ref('era_waha_integration.whatsapp_account_view_form_waha').id, 'form')])

    # -- session config pushed to WAHA -------------------------------------

    def test_engine_config_defaults_to_the_quiet_options(self):
        config = self.account._waha_webhook_config()['noweb']
        self.assertFalse(config['markOnline'])
        self.assertFalse(config['store']['fullSync'])
        self.assertTrue(config['store']['enabled'])

    def test_engine_config_follows_the_account_fields(self):
        self.account.write({'waha_mark_online': True, 'waha_noweb_full_sync': True})
        config = self.account._waha_webhook_config()['noweb']
        self.assertTrue(config['markOnline'])
        self.assertTrue(config['store']['fullSync'])

    def test_webhook_secret_is_pushed_to_waha_once_generated(self):
        self.assertNotIn('hmac', self.account._waha_webhook_config()['webhooks'][0])
        self.account.action_waha_generate_webhook_secret()
        self.assertTrue(self.account.waha_webhook_secret)
        webhook = self.account._waha_webhook_config()['webhooks'][0]
        self.assertEqual(webhook['hmac']['key'], self.account.waha_webhook_secret)

    # -- sending window ----------------------------------------------------

    def test_window_holds_cold_outreach_but_never_replies(self):
        self.account.write({
            'waha_send_window_active': True,
            'waha_send_window_start': 8.0,
            'waha_send_window_end': 21.0,
        })
        with patch.object(type(self.account), '_waha_in_send_window', return_value=False):
            self.assertTrue(self.account._waha_hold_reason(is_cold=True))
            self.assertIsNone(self.account._waha_hold_reason(is_cold=False))

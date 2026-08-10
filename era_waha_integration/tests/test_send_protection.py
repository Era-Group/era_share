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

    def _log_event_at(self, status, previous_status, when):
        """Insert a session-event row as if it had been logged `when`, without going
        through `_waha_write_status` — that would check the breaker against the *real*
        clock immediately, defeating the point of simulating history spread over hours."""
        event = self.env['whatsapp.waha.session.event'].sudo().create({
            'account_id': self.account.id,
            'status': status,
            'previous_status': previous_status,
            'source': 'webhook',
        })
        self.env.cr.execute(
            "UPDATE whatsapp_waha_session_event SET create_date = %s WHERE id = %s",
            (when, event.id))
        event.invalidate_recordset(['create_date'])
        return event

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

    def test_breaker_trips_when_a_working_session_keeps_dropping(self):
        self.account.write({'waha_flap_threshold': 3, 'waha_flap_window_minutes': 60})
        for status in ('working', 'failed', 'working', 'failed', 'working', 'failed'):
            self.account._waha_write_status(status)
        self.assertTrue(self.account.waha_paused_until,
                        "a reconnect loop must pause outbound sending")
        self.assertTrue(self.account._waha_hold_reason())

    def test_pause_alert_reaches_the_admin_from_the_webhook_context(self):
        """The breaker fires from the session.status webhook, which runs as the public
        user. _get_or_create_chat counts the caller as a member and refuses a third
        person, so the alert used to fail exactly when it mattered."""
        public = self.env.ref('base.public_user')
        account = self.account.with_user(public).sudo()
        account.write({'waha_flap_threshold': 2, 'waha_flap_window_minutes': 60})
        for status in ('working', 'failed', 'working', 'failed'):
            account._waha_write_status(status)
        self.assertTrue(account.waha_paused_until)
        admin = self.env.ref('base.user_admin')
        alert = self.env['mail.message'].sudo().search([
            ('model', '=', 'discuss.channel'),
            ('body', 'ilike', 'outbound sending paused'),
        ], limit=1)
        self.assertTrue(alert, "the administrator must be told that sending stopped")
        self.assertIn(admin.partner_id, alert.res_id and self.env['discuss.channel']
                      .browse(alert.res_id).channel_partner_ids)

    def test_pairing_a_session_does_not_trip_the_breaker(self):
        """Linking walks stopped → starting → scan_qr_code → working. Counting those as
        flaps paused sending on a session that had just come up healthy."""
        self.account.write({'waha_flap_threshold': 3, 'waha_flap_window_minutes': 60})
        for status in ('stopped', 'starting', 'scan_qr_code', 'working'):
            self.account._waha_write_status(status)
        self.assertFalse(self.account.waha_paused_until)
        self.assertIsNone(self.account._waha_hold_reason())

    def test_breaker_trips_on_sustained_low_frequency_flapping(self):
        """Hardening added after the 2026-08-05 session loss.

        That session flapped roughly once every 30-90 minutes for two straight days —
        never dense enough to trip the short (60-minute) window even once, so sending
        never stopped until WhatsApp logged the device out and it needed a fresh QR scan.
        The long window exists to catch that slow-burn pattern the short one is blind to.

        These drops never recover, so the settle-window grace added on 2026-08-10 must
        not swallow them.
        """
        self.account.write({
            'waha_flap_threshold': 6, 'waha_flap_window_minutes': 60,
            'waha_flap_long_window_hours': 6, 'waha_flap_long_window_threshold': 10,
        })
        now = fields.Datetime.now()
        for i in range(10):
            self._log_event_at('starting', 'working', now - timedelta(minutes=30 * i + 5))
        # The short window alone never sees enough of that history to trip on its own.
        self.assertLess(self.account._waha_recent_flap_count(window_minutes=60),
                         self.account.waha_flap_threshold)
        self.assertTrue(self.account._waha_check_flapping())
        self.assertTrue(self.account.waha_paused_until,
                        "sustained sparse flapping must pause outbound sending too")

    def test_a_websocket_recycle_is_not_counted_as_a_flap(self):
        """Regression for the 2026-08-10 investigation.

        GOWS drops and re-authenticates from stored credentials in the same second —
        153 drops produced 152 instant recoveries and zero re-pairings on this
        deployment. Scoring those as instability drove the health card to 'critical'
        and pushed operators into re-scanning the QR, which is the one thing that
        really does register a new device.
        """
        self.account.write({'waha_flap_threshold': 3, 'waha_flap_window_minutes': 60,
                            'waha_flap_settle_seconds': 5})
        self.account._waha_write_status('working')
        baseline = self.account.waha_flap_count
        for _ in range(6):
            self.account._waha_write_status('starting')
            self.account._waha_write_status('working')
        self.assertEqual(self.account.waha_flap_count, baseline,
                         "an instant recovery must leave the health counter where it was")
        self.assertEqual(self.account._waha_recent_flap_count(window_minutes=60), 0)
        self.assertFalse(self.account.waha_paused_until,
                         "six websocket recycles are not a reconnect loop")

    def test_a_drop_that_never_recovers_still_counts(self):
        self.account.write({'waha_flap_settle_seconds': 5})
        now = fields.Datetime.now()
        for i in range(4):
            self._log_event_at('starting', 'working', now - timedelta(minutes=5 * i + 1))
        self.assertEqual(self.account._waha_recent_flap_count(window_minutes=60), 4)

    def test_a_slow_recovery_still_counts(self):
        """A minute of downtime is a real drop, not a websocket recycle."""
        self.account.write({'waha_flap_settle_seconds': 5})
        dropped_at = fields.Datetime.now() - timedelta(minutes=10)
        self._log_event_at('starting', 'working', dropped_at)
        self._log_event_at('working', 'starting', dropped_at + timedelta(seconds=60))
        self.assertEqual(self.account._waha_recent_flap_count(window_minutes=60), 1)

    def test_a_failed_status_is_never_excused_by_a_quick_recovery(self):
        """'failed' means the engine gave up, not that a socket blinked — the settle
        grace must not reach it."""
        self.account.write({'waha_flap_settle_seconds': 5})
        dropped_at = fields.Datetime.now() - timedelta(minutes=10)
        self._log_event_at('failed', 'working', dropped_at)
        self._log_event_at('working', 'failed', dropped_at + timedelta(seconds=1))
        self.assertEqual(self.account._waha_recent_flap_count(window_minutes=60), 1)

    def test_sparse_flapping_short_of_the_long_threshold_does_not_trip(self):
        """Occasional drops over a long day must not be treated as a reconnect loop."""
        self.account.write({
            'waha_flap_threshold': 6, 'waha_flap_window_minutes': 60,
            'waha_flap_long_window_hours': 6, 'waha_flap_long_window_threshold': 10,
        })
        now = fields.Datetime.now()
        for i in range(4):
            self._log_event_at('starting', 'working', now - timedelta(hours=1.2 * i + 0.1))
        self.assertFalse(self.account._waha_check_flapping())
        self.assertFalse(self.account.waha_paused_until)

    def test_resume_clears_the_pause_and_flushes_the_queue(self):
        """Clearing the pause without waking the queue left held messages waiting up to
        a full cron interval, so Resume appeared to do nothing."""
        self.account.write({
            'waha_paused_until': fields.Datetime.now() + timedelta(hours=1),
            'waha_pause_reason': 'test',
        })
        cron = self.env.ref('whatsapp.ir_cron_send_whatsapp_queue')
        with patch.object(type(cron), '_trigger') as trigger:
            self.account.action_waha_resume_sending()
        self.assertFalse(self.account.waha_paused_until)
        self.assertIsNone(self.account._waha_hold_reason())
        self.assertTrue(trigger.called)

    def test_resume_on_an_unpaused_account_does_not_wake_the_queue(self):
        cron = self.env.ref('whatsapp.ir_cron_send_whatsapp_queue')
        with patch.object(type(cron), '_trigger') as trigger:
            self.account.action_waha_resume_sending()
        self.assertFalse(trigger.called)

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

    # -- QR retrieval ------------------------------------------------------

    def test_qr_endpoint_matches_the_engine(self):
        """/api/screenshot photographs a browser, so it never yields a QR on NOWEB."""
        self.account.waha_engine = 'NOWEB'
        endpoint, params = self.account._waha_qr_request()
        self.assertEqual(endpoint, 'protection-test/auth/qr')
        self.assertEqual(params['format'], 'image')
        self.account.waha_engine = 'WEBJS'
        self.assertEqual(self.account._waha_qr_request()[0], 'screenshot')

    def test_qr_button_explains_itself_when_there_is_nothing_to_scan(self):
        with patch.object(type(self.account), '_waha_request',
                          side_effect=UserError('Session status is not as expected')):
            with self.assertRaises(UserError):
                self.account.action_waha_show_qr()
            # The webhook-driven path must stay quiet — it fires on every status event.
            self.assertIsNone(self.account.action_waha_get_qr())

    def test_scan_qr_code_status_fetches_a_fresh_code(self):
        with patch.object(type(self.account), '_waha_request',
                          return_value=b'PNGDATA') as request:
            self.account._waha_apply_status('scan_qr_code', {}, source='webhook')
        self.assertTrue(request.called)
        self.assertTrue(self.account.waha_qr_fetched)

    # -- ack matching across engines ---------------------------------------

    def _outbound_with_uid(self, uid):
        channel = self._make_channel('15550009001')
        message = self.env['mail.message'].sudo().create({
            'model': 'discuss.channel', 'res_id': channel.id, 'body': 'x',
            'message_type': 'whatsapp_message', 'author_id': self.agent.partner_id.id,
        })
        return self.env['whatsapp.message'].sudo().create({
            'mail_message_id': message.id, 'wa_account_id': self.account.id,
            'message_type': 'outbound', 'state': 'sent', 'msg_uid': uid,
        })

    def test_ack_matches_when_gows_reserialises_the_id(self):
        """GOWS rebuilds the id for acks from the delivery receipt: the fromMe flag is
        inverted and the chat is addressed by its @lid alias. Only the trailing hash
        survives, so ticks would otherwise never advance past 'sent'."""
        wa = self._outbound_with_uid('true_966582595227@c.us_3EB0998A050D1D96C207E6')
        found = self.account._waha_find_message(
            'false_203779773329574@lid_3EB0998A050D1D96C207E6')
        self.assertEqual(found, wa)

    def test_ack_still_matches_the_plain_noweb_shapes(self):
        wa = self._outbound_with_uid('3EB0AAA111')
        self.assertEqual(
            self.account._waha_find_message('true_966582595227@c.us_3EB0AAA111'), wa)
        wa2 = self._outbound_with_uid('true_966582595227@c.us_3EB0BBB222')
        self.assertEqual(
            self.account._waha_find_message('true_966582595227@c.us_3EB0BBB222'), wa2)

    def test_unknown_hash_matches_nothing(self):
        self._outbound_with_uid('true_966582595227@c.us_3EB0CCC333')
        self.assertFalse(self.account._waha_find_message('true_x@c.us_3EB0NOPE'))

    def test_group_ids_deduplicate_by_message_hash_not_sender_lid(self):
        first = 'false_120363430227532375@g.us_3EB0FIRST_260400176201908@lid'
        later = 'false_120363430227532375@g.us_3EB0LATER_260400176201908@lid'
        self._outbound_with_uid(first)
        self.assertTrue(self.account._waha_uid_exists(first))
        self.assertFalse(self.account._waha_uid_exists(later))
        self.assertFalse(self.account._waha_find_message(later))

    def test_group_ack_matches_across_participant_jids(self):
        stored = 'true_120363430227532375@g.us_3EB0GROUPHASH_966557428221@c.us'
        wa = self._outbound_with_uid(stored)
        self.assertEqual(
            self.account._waha_find_message(
                'false_120363430227532375@g.us_3EB0GROUPHASH_260400176201908@lid'),
            wa)

    # -- session lifecycle -------------------------------------------------

    def test_stop_pauses_without_unlinking_the_device(self):
        """DELETE /api/sessions/{name} logs the device out and wipes credentials, so
        Stop must never use it — that turned a pause into a forced re-pairing."""
        with patch.object(type(self.account), '_waha_request', return_value={}) as request:
            self.account.action_waha_stop_session()
        endpoint, method = request.call_args[0][0], request.call_args[0][1]
        self.assertEqual(endpoint, 'sessions/protection-test/stop')
        self.assertEqual(method, 'POST')

    def test_unlink_uses_the_logout_endpoint(self):
        with patch.object(type(self.account), '_waha_request', return_value={}) as request:
            self.account.action_waha_logout_session()
        self.assertEqual(request.call_args[0][0], 'sessions/protection-test/logout')

    def test_starting_an_existing_session_actually_starts_it(self):
        """A 422 means the session exists; updating its config alone leaves it down."""
        response = type('R', (), {'status_code': 422})()
        with patch.object(type(self.account), '_waha_http', return_value=response), \
             patch.object(type(self.account), '_waha_request', return_value={}) as request, \
             patch.object(type(self.account), 'action_waha_get_qr', return_value=None):
            self.account.action_waha_start_session()
        called = [call[0][0] for call in request.call_args_list]
        self.assertIn('sessions/protection-test', called)
        self.assertIn('sessions/protection-test/start', called)

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

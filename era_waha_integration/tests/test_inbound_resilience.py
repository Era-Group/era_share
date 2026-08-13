# Part of Era Group custom addons.
from unittest.mock import patch

import psycopg2.errors

from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestWahaInboundResilience(TransactionCase):
    """Regression tests for the 2026-08-13 inbound message loss.

    A SerializationFailure raised while posting an inbound message was swallowed
    by ``_waha_process_incoming``, so the webhook answered 200 and WAHA never
    redelivered — the customer message was lost. Concurrency errors must
    propagate (Odoo's request dispatcher retries them in-process); every other
    error keeps the old swallow-and-log behaviour."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = new_test_user(cls.env, login='waha_resil_agent', name='WAHA Resilience Agent')
        cls.account = cls.env['whatsapp.account'].sudo().create({
            'name': 'WAHA Resilience Test',
            'provider': 'waha',
            'waha_server_url': 'http://waha.test',
            'waha_session': 'resilience-test',
            'notify_user_ids': cls.agent.ids,
            'waha_check_number_exists': False,
        })

    def _payload(self, uid, number='966555000901'):
        return {
            'id': 'false_%s@c.us_%s' % (number, uid),
            'from': '%s@c.us' % number,
            'fromMe': False,
            'type': 'text',
            'body': 'hello from customer',
        }

    def test_concurrency_error_propagates(self):
        with patch.object(
            type(self.env['discuss.channel']), 'message_post',
            side_effect=psycopg2.errors.SerializationFailure('concurrent update'),
        ):
            with self.assertRaises(psycopg2.errors.SerializationFailure):
                self.account._waha_process_incoming(self._payload('SER1'))

    def test_other_errors_still_swallowed(self):
        with patch.object(
            type(self.env['discuss.channel']), 'message_post',
            side_effect=ValueError('unexpected payload'),
        ):
            with self.assertLogs(
                'odoo.addons.era_waha_integration.models.whatsapp_account', level='ERROR',
            ) as logs:
                self.account._waha_process_incoming(self._payload('VAL1'))
        self.assertTrue(any('failed posting inbound message' in line for line in logs.output))

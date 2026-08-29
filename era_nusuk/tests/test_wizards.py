# -*- coding: utf-8 -*-

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_phase1 import TestPhase1Base


@tagged('post_install', '-at_install')
class TestBulkHotelBookingWizard(TestPhase1Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.trip = cls.env['umrah.trip'].create({
            'name': 'Wizard Trip', 'departure_city': 'Cairo',
            'departure_date': '2026-08-01', 'return_date': '2026-08-10',
            'max_capacity': 40, 'base_price': 1000.0,
        })
        cls.group.trip_ids = [(4, cls.trip.id)]

    def _wizard(self, **extra):
        vals = {
            'trip_id': self.trip.id,
            'hotel_id': self.hotel.id,
            'room_type_id': self.room_type.id,
            'check_in_date': '2026-08-01',
            'check_out_date': '2026-08-10',
        }
        vals.update(extra)
        return self.env['umrah.bulk.hotel.booking.wizard'].create(vals)

    def test_creates_one_booking_per_group(self):
        action = self._wizard(group_id=self.group.id).action_create_bookings()
        bookings = self.env['umrah.hotel.booking'].search(action['domain'])
        self.assertEqual(len(bookings), 1)
        self.assertEqual(bookings.group_ids, self.group)
        # Rate falls back to the room type's base price when left empty.
        self.assertEqual(bookings.room_rate_per_night, 400.0)
        self.assertTrue(self.group._hotel_coverage_ok())

    def test_uses_trip_groups_when_no_group_selected(self):
        group2 = self.env['umrah.group'].create({
            'name': 'Wizard Group 2', 'max_capacity': 10,
            'trip_ids': [(4, self.trip.id)],
        })
        action = self._wizard(room_rate_per_night=350.0).action_create_bookings()
        bookings = self.env['umrah.hotel.booking'].search(action['domain'])
        self.assertEqual(len(bookings), 2)
        self.assertEqual(bookings.group_ids, self.group | group2)
        self.assertEqual(set(bookings.mapped('room_rate_per_night')), {350.0})

    def test_bad_dates_blocked(self):
        wizard = self._wizard(group_id=self.group.id,
                              check_out_date='2026-08-01')
        with self.assertRaises(UserError):
            wizard.action_create_bookings()

    def test_no_groups_blocked(self):
        trip2 = self.env['umrah.trip'].create({
            'name': 'Empty Trip', 'departure_city': 'Amman',
            'departure_date': '2026-08-01', 'return_date': '2026-08-10',
            'max_capacity': 10, 'base_price': 500.0,
        })
        with self.assertRaises(UserError):
            self._wizard(trip_id=trip2.id).action_create_bookings()


@tagged('post_install', '-at_install')
class TestBulkAgentInvoicing(TestPhase1Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pilgrim2 = cls.env['umrah.pilgrim'].create({
            'full_name': 'Bulk Pilgrim 2', 'nationality': cls.env.ref('base.sa').id,
            'gender': 'female', 'birth_date': '1992-02-02',
            'mobile': '0500000021', 'passport_number': 'B7654321',
        })
        cls.visa_a = cls._make_fee_visa(cls.pilgrim, 300.0, 50.0)
        cls.visa_b = cls._make_fee_visa(cls.pilgrim2, 300.0, 75.0)

    @classmethod
    def _make_fee_visa(cls, pilgrim, visa_fee, service_fee):
        return cls.env['umrah.visa'].create({
            'pilgrim_id': pilgrim.id, 'agent_id': cls.agent.id,
            'group_id': cls.group.id, 'application_date': '2026-07-01',
            'visa_fee': visa_fee, 'service_fee': service_fee,
        })

    def test_wizard_invoices_and_links_visas(self):
        wizard = self.env['umrah.bulk.invoice.visa.wizard'].create({
            'agent_id': self.agent.id})
        action = wizard.action_create_bulk_agent_invoices()
        invoices = self.env['account.move'].search(action['domain'])
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.partner_id, self.agent.partner_id)
        self.assertEqual(invoices.agent_id, self.agent)
        self.assertEqual(len(invoices.invoice_line_ids), 2)
        self.assertAlmostEqual(invoices.amount_untaxed, 725.0)  # 350 + 375
        for visa in (self.visa_a, self.visa_b):
            self.assertEqual(visa.status, 'invoiced')
            self.assertEqual(visa.invoice_id, invoices)

    def test_wizard_does_not_reinvoice(self):
        wizard = self.env['umrah.bulk.invoice.visa.wizard'].create({
            'agent_id': self.agent.id})
        wizard.action_create_bulk_agent_invoices()
        with self.assertRaises(UserError):
            wizard.action_create_bulk_agent_invoices()

    def test_wizard_status_filter(self):
        self.visa_a.action_submit()
        wizard = self.env['umrah.bulk.invoice.visa.wizard'].create({
            'agent_id': self.agent.id, 'status': 'submitted'})
        action = wizard.action_create_bulk_agent_invoices()
        invoices = self.env['account.move'].search(action['domain'])
        self.assertEqual(len(invoices.invoice_line_ids), 1)
        self.assertEqual(self.visa_a.status, 'invoiced')
        self.assertEqual(self.visa_b.status, 'draft')

    def test_legacy_list_action(self):
        visas = self.visa_a | self.visa_b
        action = visas.with_context(
            active_ids=visas.ids).action_create_bulk_agent_invoices()
        invoices = self.env['account.move'].search(action['domain'])
        self.assertEqual(len(invoices), 1)
        self.assertEqual(len(invoices.invoice_line_ids), 2)
        self.assertEqual(visas.mapped('status'), ['invoiced', 'invoiced'])
        # Nothing left to invoice → legacy entry point refuses politely.
        with self.assertRaises(UserError):
            visas.with_context(
                active_ids=visas.ids).action_create_bulk_agent_invoices()

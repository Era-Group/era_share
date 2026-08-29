# -*- coding: utf-8 -*-

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPhase1Base(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env['ir.config_parameter'].sudo()
        # Neutral defaults per test class; individual tests flip them.
        icp.set_param('era_nusuk.enforce_hotel_coverage', 'False')
        icp.set_param('era_nusuk.enforce_transport_before_visa', 'False')
        icp.set_param('era_nusuk.enforce_agent_quota', 'False')

        cls.occupancy_quad = cls.env.ref('era_nusuk.occupancy_quad')
        cls.occupancy_double = cls.env.ref('era_nusuk.occupancy_double')
        cls.tier_standard = cls.env.ref('era_nusuk.agent_tier_standard')
        cls.tier_vip = cls.env.ref('era_nusuk.agent_tier_vip')

        cls.agent = cls.env['umrah.agent'].create({
            'name': 'Phase1 Agent',
            'phone': '0500000010',
            'email': 'phase1@test.example',
            'country_id': cls.env.ref('base.sa').id,
            'tier_id': cls.tier_standard.id,
        })
        cls.pilgrim = cls.env['umrah.pilgrim'].create({
            'full_name': 'Phase1 Pilgrim',
            'nationality': cls.env.ref('base.sa').id,
            'gender': 'male',
            'birth_date': '1990-01-01',
            'mobile': '0500000011',
            'passport_number': 'P1234567',
        })
        cls.group = cls.env['umrah.group'].create({
            'name': 'Phase1 Group',
            'max_capacity': 10,
            'agent_id': cls.agent.id,
            'arrival_date': '2026-08-01',
            'departure_date': '2026-08-10',
        })
        cls.hotel = cls.env['umrah.hotel'].create({
            'name': 'Phase1 Hotel',
            'city': 'mecca',
            'address': 'Makkah',
            'tourism_license_number': 'TL-123',
        })
        cls.room_type = cls.env['umrah.hotel.room.type'].create({
            'name': 'Quad Room',
            'hotel_id': cls.hotel.id,
            'capacity': 4,
            'bed_type': 'quad',
            'base_price_per_night': 400.0,
        })

    def _make_visa(self, **extra):
        vals = {
            'pilgrim_id': self.pilgrim.id,
            'application_date': '2026-07-01',
            'agent_id': self.agent.id,
            'group_id': self.group.id,
        }
        vals.update(extra)
        return self.env['umrah.visa'].create(vals)

    def _make_booking(self, check_in, check_out):
        return self.env['umrah.hotel.booking'].create({
            'hotel_id': self.hotel.id,
            'room_type_id': self.room_type.id,
            'group_ids': [(6, 0, [self.group.id])],
            'check_in_date': check_in,
            'check_out_date': check_out,
            'room_rate_per_night': 400.0,
        })


class TestContractRename(TestPhase1Base):

    def test_contract_model_renamed(self):
        Contract = self.env['umrah.customer.contract']
        self.assertEqual(Contract._table, 'umrah_customer_contract')
        # Pre-existing contracts survived the SQL migration.
        self.assertTrue(Contract.search_count([]) >= 0)
        self.assertNotIn('umrah_customrt_contract', self.env)


class TestPackagePricing(TestPhase1Base):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.package = cls.env['umrah.package'].create({
            'name': 'Test Package',
            'nights_makkah': 4,
            'nights_madinah': 3,
            'price_line_ids': [
                (0, 0, {'occupancy_type_id': cls.occupancy_quad.id,
                        'sale_price': 1000.0, 'cost_price': 700.0}),
                (0, 0, {'occupancy_type_id': cls.occupancy_quad.id,
                        'tier_id': cls.tier_vip.id,
                        'sale_price': 900.0, 'cost_price': 700.0}),
            ],
        })

    def test_code_sequence_and_nights(self):
        self.assertTrue(self.package.code.startswith('PKG-'))
        self.assertEqual(self.package.total_nights, 7)

    def test_generic_price(self):
        sale, cost = self.package.get_price(self.occupancy_quad)
        self.assertEqual((sale, cost), (1000.0, 700.0))

    def test_tier_price_overrides_generic(self):
        sale, _cost = self.package.get_price(
            self.occupancy_quad, tier=self.tier_vip)
        self.assertEqual(sale, 900.0)

    def test_tier_without_specific_line_falls_back(self):
        sale, _cost = self.package.get_price(
            self.occupancy_quad, tier=self.tier_standard)
        self.assertEqual(sale, 1000.0)

    def test_missing_price_raises(self):
        with self.assertRaises(UserError):
            self.package.get_price(self.occupancy_double)


class TestMasarConstraints(TestPhase1Base):

    def test_hotel_coverage_blocks_submit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'era_nusuk.enforce_hotel_coverage', 'True')
        visa = self._make_visa()
        with self.assertRaises(UserError):
            visa.action_submit()

    def test_hotel_coverage_gap_blocks_submit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'era_nusuk.enforce_hotel_coverage', 'True')
        # Booking covers only part of the stay (gap before departure).
        self._make_booking('2026-08-01', '2026-08-05')
        visa = self._make_visa()
        with self.assertRaises(UserError):
            visa.action_submit()

    def test_full_hotel_coverage_passes(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'era_nusuk.enforce_hotel_coverage', 'True')
        self._make_booking('2026-08-01', '2026-08-05')
        self._make_booking('2026-08-05', '2026-08-10')
        visa = self._make_visa()
        visa.action_submit()
        self.assertEqual(visa.status, 'submitted')

    def test_disabled_check_passes_without_bookings(self):
        visa = self._make_visa()
        visa.action_submit()
        self.assertEqual(visa.status, 'submitted')

    def test_transport_check(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'era_nusuk.enforce_transport_before_visa', 'True')
        visa = self._make_visa()
        with self.assertRaises(UserError):
            visa.action_submit()
        for mtype in ('arrival', 'departure'):
            self.env['umrah.movement'].create({
                'movement_type': mtype,
                'group_ids': [(6, 0, [self.group.id])],
            })
        visa.action_submit()
        self.assertEqual(visa.status, 'submitted')

    def test_agent_quota(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'era_nusuk.enforce_agent_quota', 'True')
        self.agent.seasonal_quota = 1
        visa1 = self._make_visa()
        visa1.action_submit()
        visa2 = self._make_visa()
        with self.assertRaises(UserError):
            visa2.action_submit()


class TestRooming(TestPhase1Base):

    def test_room_capacity_constraint(self):
        booking = self._make_booking('2026-08-01', '2026-08-10')
        members = self.env['umrah.group.member']
        for i in range(3):
            pilgrim = self.env['umrah.pilgrim'].create({
                'full_name': 'Roomer %s' % i,
                'nationality': self.env.ref('base.sa').id,
                'gender': 'male',
                'birth_date': '1990-01-01',
                'mobile': '05000001%s2' % i,
                'passport_number': 'R12345%s' % i,
            })
            trip = self.env['umrah.trip'].create({
                'name': 'Trip %s' % i,
                'departure_city': 'Cairo',
                'departure_date': '2026-08-01',
                'return_date': '2026-08-10',
                'max_capacity': 10,
                'base_price': 100.0,
            })
            members |= self.env['umrah.group.member'].create({
                'pilgrim_id': pilgrim.id,
                'group_id': self.group.id,
                'trip_id': trip.id,
            })
        room = self.env['umrah.hotel.booking.room'].create({
            'booking_id': booking.id,
            'room_number': '101',
            'occupancy_type_id': self.occupancy_double.id,
            'member_ids': [(6, 0, members[:2].ids)],
        })
        self.assertEqual(room.occupants, 2)
        with self.assertRaises(ValidationError):
            room.member_ids = [(6, 0, members.ids)]  # 3 > double capacity 2

    def test_movement_sequence_and_type(self):
        move = self.env['umrah.movement'].create({
            'movement_type': 'intercity',
            'group_ids': [(6, 0, [self.group.id])],
        })
        self.assertTrue(move.name.startswith('MOV'))
        self.assertEqual(move.movement_type, 'intercity')

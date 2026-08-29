# -*- coding: utf-8 -*-

import base64
from io import BytesIO

from openpyxl import Workbook

from odoo.tests import TransactionCase, tagged


def _build_nusuk_xlsx(visa_fees_col14, grand_total_col24):
    """Build a Nusuk-shaped workbook: 24 columns, header in row 1, data from
    row 2. Numeric cells are stored as TEXT using the Arabic decimal separator
    (٫) and assorted bidi marks / thousands separators, exactly like the real
    export. ``visa_fees_col14`` and ``grand_total_col24`` are lists of the raw
    cell strings to place in columns 14 and 24 respectively (one per data row).
    """
    wb = Workbook()
    ws = wb.active
    header = [''] * 24
    header[0] = 'رقم الفاتورة'
    header[2] = 'رقم الوكيل'
    header[5] = 'رقم المجموعة'
    header[7] = 'عدد المعتمرين'
    header[13] = 'رسوم التأشيرة'      # col 14
    header[23] = 'المبلغ الاجمالي'    # col 24
    ws.append(header)
    for fee, grand in zip(visa_fees_col14, grand_total_col24):
        row = [''] * 24
        row[13] = fee
        row[23] = grand
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return base64.b64encode(buf.getvalue())


@tagged('post_install', '-at_install')
class TestNusukParser(TransactionCase):

    def _new_visa(self, **vals):
        # In-memory record: avoids required-field setup (pilgrim, etc.) while
        # still exercising the real compute / onchange logic.
        return self.env['umrah.visa'].new(vals)

    def test_parser_sums_both_columns(self):
        # col 14 sum = 675.00 ; col 24 sum = 3750.75
        blob = _build_nusuk_xlsx(
            visa_fees_col14=['225٫00', '225٫00', '225٫00'],
            grand_total_col24=['‏1٬125٫00', '750٫50', '1 875٫25'],
        )
        visa = self._new_visa(nusuk_file=blob)
        fees, grand = visa._parse_nusuk_totals()
        self.assertAlmostEqual(fees, 675.00, places=2,
                               msg="Col 14 (visa fees) sum is wrong")
        self.assertAlmostEqual(grand, 3750.75, places=2,
                               msg="Col 24 (grand total) sum is wrong")

    def test_zero_total_gives_config_warning_not_mismatch(self):
        # No sale price -> total == 0. Uploading a non-zero Nusuk file must warn
        # about the missing price config, NOT a data mismatch.
        blob = _build_nusuk_xlsx(['225٫00'], ['1000٫00'])
        visa = self._new_visa(sale_price=0.0, visa_count=5, nusuk_file=blob)
        result = visa._onchange_check_nusuk_total()
        self.assertTrue(result and 'warning' in result)
        self.assertIn('Cannot Verify', result['warning']['title'])

    def test_real_mismatch_warning_reports_both_totals(self):
        # total = 100 * 2 = 200 ; visa-fees = 675 ; grand = 900 -> matches
        # neither -> genuine mismatch, and the warning shows both values.
        blob = _build_nusuk_xlsx(['225٫00', '225٫00', '225٫00'],
                                 ['300٫00', '300٫00', '300٫00'])
        visa = self._new_visa(sale_price=100.0, visa_count=2, nusuk_file=blob)
        result = visa._onchange_check_nusuk_total()
        self.assertTrue(result and 'warning' in result)
        self.assertIn('Mismatch', result['warning']['title'])
        self.assertIn('675', result['warning']['message'])
        self.assertIn('900', result['warning']['message'])

    def test_matching_visa_fees_no_warning(self):
        # total = 225 * 3 = 675 matches col 14 -> no warning.
        blob = _build_nusuk_xlsx(['225٫00', '225٫00', '225٫00'],
                                 ['300٫00', '300٫00', '300٫00'])
        visa = self._new_visa(sale_price=225.0, visa_count=3, nusuk_file=blob)
        result = visa._onchange_check_nusuk_total()
        self.assertFalse(result, msg="Matching visa-fees total must not warn")

    def test_matching_grand_total_no_warning(self):
        # total = 300 * 3 = 900 matches col 24 (not col 14) -> no warning,
        # because the check accepts either column (confirmed 2026-07-20).
        blob = _build_nusuk_xlsx(['225٫00', '225٫00', '225٫00'],
                                 ['300٫00', '300٫00', '300٫00'])
        visa = self._new_visa(sale_price=300.0, visa_count=3, nusuk_file=blob)
        result = visa._onchange_check_nusuk_total()
        self.assertFalse(result, msg="Matching grand total must not warn")


@tagged('post_install', '-at_install')
class TestVisaInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tax15 = cls.env['account.tax'].search([
            ('type_tax_use', '=', 'sale'),
            ('amount', '=', 15),
            ('company_id', '=', cls.env.company.id),
        ], limit=1)
        if not cls.tax15:
            cls.tax15 = cls.env['account.tax'].create({
                'name': 'VAT 15% (test)',
                'type_tax_use': 'sale',
                'amount_type': 'percent',
                'amount': 15,
            })
        cls.pilgrim = cls.env['umrah.pilgrim'].create({
            'full_name': 'Test Pilgrim',
            'nationality': cls.env.ref('base.sa').id,
            'gender': 'male',
            'birth_date': '1990-01-01',
            'mobile': '0500000001',
            'passport_number': 'T1234567',
        })

    def _make_agent(self, billing_type):
        return self.env['umrah.agent'].create({
            'name': 'Agent %s' % billing_type,
            'phone': '0500000000',
            'email': 'agent-%s@test.example' % billing_type,
            'country_id': self.env.ref('base.sa').id,
            'agent_billing_type': billing_type,
        })

    def _make_visa(self, agent, sale, purchase, count):
        return self.env['umrah.visa'].create({
            'pilgrim_id': self.pilgrim.id,
            'application_date': '2026-07-01',
            'agent_id': agent.id,
            'sale_price': sale,
            'purchase_price': purchase,
            'visa_count': count,
        })

    def test_default_agent_invoice_totals_sale_price(self):
        # CONFIRMED 2026-07-20: two lines — visa fees (no VAT) + service
        # margin (15% VAT). Untaxed total must equal sale x count, NOT 2x.
        agent = self._make_agent('default')
        visa = self._make_visa(agent, sale=300.0, purchase=225.0, count=2)
        visa.action_create_visa_invoice()
        move = visa.invoice_id
        self.assertEqual(len(move.invoice_line_ids), 2)
        self.assertAlmostEqual(move.amount_untaxed, 600.0, places=2,
                               msg="Untaxed total must equal sale price x count")
        fees_line = move.invoice_line_ids.filtered(lambda l: not l.tax_ids)
        margin_line = move.invoice_line_ids - fees_line
        self.assertAlmostEqual(sum(fees_line.mapped('price_subtotal')), 450.0,
                               places=2)
        self.assertAlmostEqual(sum(margin_line.mapped('price_subtotal')), 150.0,
                               places=2)
        # VAT applies to the margin only: 15% of 150 = 22.50.
        self.assertAlmostEqual(move.amount_tax, 22.50, places=2)

    def test_actual_agent_invoice_purchase_only(self):
        agent = self._make_agent('actual')
        visa = self._make_visa(agent, sale=300.0, purchase=225.0, count=2)
        visa.action_create_visa_invoice()
        move = visa.invoice_id
        self.assertEqual(len(move.invoice_line_ids), 1)
        self.assertAlmostEqual(move.amount_untaxed, 450.0, places=2)
        self.assertFalse(move.invoice_line_ids.tax_ids)

from odoo import Command, fields
from odoo.tests import TransactionCase


class CommissionCommon(TransactionCase):
    """The fixture every commission test builds on.

    One customer, two products in two categories, one agent with a manager
    above them, and a plan whose rules cover both categories. Kept apart from
    the test classes so a second suite reuses it without re-running the first
    suite's tests.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.currency = cls.company.currency_id
        cls.env.user.group_ids |= cls.env.ref(
            'era_sales_commission.group_era_commission_manager')

        cls.date_from = fields.Date.to_date('2026-03-01')
        cls.date_to = fields.Date.to_date('2026-03-31')
        cls.order_date = fields.Date.to_date('2026-03-10')

        cls.categ_fabric = cls.env['product.category'].create({'name': 'Fabrics'})
        cls.categ_accessory = cls.env['product.category'].create({'name': 'Accessories'})

        cls.product_fabric = cls.env['product.product'].create({
            'name': 'Poplin Roll',
            'categ_id': cls.categ_fabric.id,
            'list_price': 100.0,
            'standard_price': 60.0,
            'taxes_id': [Command.clear()],
        })
        cls.product_button = cls.env['product.product'].create({
            'name': 'Shell Button',
            'categ_id': cls.categ_accessory.id,
            'list_price': 10.0,
            'standard_price': 4.0,
            'taxes_id': [Command.clear()],
        })
        cls.product_shipping = cls.env['product.product'].create({
            'name': 'Shipping',
            'type': 'service',
            'categ_id': cls.categ_accessory.id,
            'list_price': 50.0,
            'taxes_id': [Command.clear()],
        })

        cls.customer = cls.env['res.partner'].create({'name': 'Al Nahda Trading'})
        cls.agent_partner = cls.env['res.partner'].create({'name': 'Rep Payable'})

        cls.manager = cls.env['era.commission.agent'].create({
            'name': 'Sales Manager',
            'override_rate': 10.0,
            'override_basis': 'commission',
        })
        cls.agent = cls.env['era.commission.agent'].create({
            'name': 'Field Rep',
            'partner_id': cls.agent_partner.id,
            'parent_id': cls.manager.id,
        })

        cls.plan = cls.env['era.commission.plan'].create({
            'name': 'Standard Invoice Plan',
            'basis': 'sales',
            'rule_ids': [
                Command.create({
                    'name': '5% on fabrics',
                    'sequence': 10,
                    'product_categ_id': cls.categ_fabric.id,
                    'calc_type': 'percent',
                    'rate': 5.0,
                }),
                Command.create({
                    'name': '2% on everything else',
                    'sequence': 20,
                    'calc_type': 'percent',
                    'rate': 2.0,
                }),
            ],
        })
        cls.plan.action_approve()
        cls._assign(cls.agent, cls.plan)

        cls.Engine = cls.env['era.commission.engine']

    # ------------------------------------------------------------------
    @classmethod
    def _assign(cls, agent, plan, share=100.0, date_from=None):
        return cls.env['era.commission.agent.plan'].create({
            'agent_id': agent.id,
            'plan_id': plan.id,
            'share': share,
            'date_from': date_from,
        })

    @classmethod
    def _new_plan(cls, name, **values):
        """A plan with a single 10% rule unless the test says otherwise."""
        rules = values.pop('rule_ids', [Command.create({
            'name': '10% flat', 'calc_type': 'percent', 'rate': 10.0,
        })])
        plan = cls.env['era.commission.plan'].create(
            dict({'name': name, 'rule_ids': rules}, **values))
        plan.action_approve()
        return plan

    @classmethod
    def _rate(cls, agent, commission_type, rate=0.0, unit_price=0.0, product=None):
        """What this agent earns on this kind of commission."""
        return cls.env['era.commission.agent.rate'].create({
            'agent_id': agent.id,
            'commission_type': commission_type,
            'rate': rate,
            'unit_price': unit_price,
            'product_id': product.id if product else False,
        })

    @classmethod
    def _tier(cls, product, qty_from, qty_to, unit_price):
        return cls.env['era.commission.unit.price.tier'].create({
            'product_id': product.id,
            'qty_from': qty_from,
            'qty_to': qty_to,
            'unit_price': unit_price,
        })

    @classmethod
    def _target(cls, agent, commission_type, amount=0.0, quantity=0.0,
                plan=None, product=None, date_from=None, date_to=None):
        return cls.env['era.commission.target'].create({
            'agent_id': agent.id,
            'commission_type': commission_type,
            'plan_id': plan.id if plan else False,
            'product_id': product.id if product else False,
            'date_from': date_from or cls.date_from,
            'date_to': date_to or cls.date_to,
            'target_amount': amount,
            'target_qty': quantity,
        })

    @classmethod
    def _sale_tax(cls, rate=15.0):
        tax = cls.env['account.tax'].search([
            ('company_id', '=', cls.company.id),
            ('type_tax_use', '=', 'sale'),
            ('amount_type', '=', 'percent'),
            ('amount', '=', rate),
            ('price_include_override', '=', 'tax_excluded'),
        ], limit=1)
        return tax or cls.env['account.tax'].create({
            'name': f'ERA VAT {rate:g}%',
            'type_tax_use': 'sale',
            'amount_type': 'percent',
            'amount': rate,
            'price_include_override': 'tax_excluded',
            'company_id': cls.company.id,
        })

    @classmethod
    def _make_order(cls, lines=None, agent=None, date=None, confirm=True, taxes=None):
        lines = lines or [(cls.product_fabric, 10, 100.0)]
        order = cls.env['sale.order'].create({
            'partner_id': cls.customer.id,
            'date_order': fields.Datetime.to_datetime(
                f'{date or cls.order_date} 09:00:00'),
            'era_agent_id': (agent or cls.agent).id,
            'order_line': [
                Command.create({
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'price_unit': price,
                    'tax_ids': ([Command.set(taxes.ids)] if taxes
                                else [Command.clear()]),
                })
                for product, qty, price in lines
            ],
        })
        if confirm:
            order.action_confirm()
            order.date_order = fields.Datetime.to_datetime(
                f'{date or cls.order_date} 09:00:00')
        return order

    @classmethod
    def _make_invoice(cls, order, post=True, date=None):
        invoice = order._create_invoices()
        invoice.invoice_date = date or cls.order_date
        if post:
            invoice.action_post()
        return invoice

    @classmethod
    def _make_refund(cls, invoice, date=None, quantity=None):
        """A posted credit note of the invoice, of everything or of a quantity."""
        reversal = invoice._reverse_moves([{
            'invoice_date': date or cls.order_date,
            'date': date or cls.order_date,
        }])
        if quantity is not None:
            product_lines = reversal.line_ids.filtered(
                lambda line: line.display_type == 'product')
            product_lines[0].quantity = quantity
        reversal.action_post()
        return reversal

    @classmethod
    def _pay(cls, invoice, amount, date=None, agent=None):
        """Register a payment of ``amount`` against a posted invoice."""
        wizard = cls.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({
            'amount': amount,
            'payment_date': date or cls.order_date,
        })
        payment = wizard._create_payments()
        if agent is not None:
            payment.era_agent_id = agent
        return payment

    @classmethod
    def _employee(cls, name='Field Rep'):
        return cls.env['hr.employee'].create({
            'name': name,
            'company_id': cls.company.id,
        })

    @classmethod
    def _payslip(cls, employee, date_from=None, date_to=None):
        return cls.env['hr.payslip'].create({
            'name': f'Payslip {employee.name}',
            'employee_id': employee.id,
            'date_from': date_from or cls.date_from,
            'date_to': date_to or cls.date_to,
            'struct_id': cls.env.ref('hr_payroll.structure_002').id,
        })

    def _lines(self, agent=None, line_type='sale'):
        domain = [('company_id', '=', self.company.id)]
        if agent is not None:
            domain.append(('agent_id', '=', agent.id))
        if line_type:
            domain.append(('line_type', '=', line_type))
        return self.env['era.commission.line'].search(domain)

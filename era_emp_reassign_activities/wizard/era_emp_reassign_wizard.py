import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class EraEmpReassignWizard(models.TransientModel):
    _name = 'era.emp.reassign.wizard'
    _description = "Reassign Employee Work"

    from_user_id = fields.Many2one(
        'res.users',
        string="From User",
        required=True,
        help="The user whose pending work will be transferred.",
    )
    to_user_id = fields.Many2one(
        'res.users',
        string="To User",
        required=True,
        domain="[('share', '=', False), ('active', '=', True), ('id', '!=', from_user_id)]",
        help="The user who will take over the pending work.",
    )

    only_future = fields.Boolean(
        string="Only Open Records",
        default=True,
        help=(
            "If checked, closed business records (won/lost leads, "
            "closed tickets, past calendar events...) are skipped. "
            "Pending activities are always reassigned regardless of this flag, "
            "and regardless of their parent record's state — a won lead keeps "
            "its salesperson, but its scheduled activity still moves to the "
            "new user. Quotations are always limited to draft/sent state."
        ),
    )

    reassign_activities = fields.Boolean(string="Scheduled Activities", default=True)
    reassign_leads = fields.Boolean(string="CRM Leads / Opportunities", default=True)
    reassign_calendar = fields.Boolean(string="Calendar Events", default=True)
    reassign_helpdesk = fields.Boolean(string="Helpdesk Tickets", default=True)
    reassign_tasks = fields.Boolean(string="Project Tasks", default=True)
    reassign_sale_orders = fields.Boolean(string="Quotations", default=True)
    reassign_rfqs = fields.Boolean(string="Requests for Quotation", default=True)
    reassign_partners = fields.Boolean(string="Customers (Salesperson)", default=True)
    reassign_invoices = fields.Boolean(string="Draft Invoices (Salesperson)", default=True)

    has_helpdesk = fields.Boolean(compute='_compute_module_availability')
    has_crm = fields.Boolean(compute='_compute_module_availability')
    has_calendar = fields.Boolean(compute='_compute_module_availability')
    has_project = fields.Boolean(compute='_compute_module_availability')
    has_sale = fields.Boolean(compute='_compute_module_availability')
    has_purchase = fields.Boolean(compute='_compute_module_availability')
    has_account = fields.Boolean(compute='_compute_module_availability')

    activities_count = fields.Integer(compute='_compute_counts')
    leads_count = fields.Integer(compute='_compute_counts')
    calendar_count = fields.Integer(compute='_compute_counts')
    helpdesk_count = fields.Integer(compute='_compute_counts')
    tasks_count = fields.Integer(compute='_compute_counts')
    sale_orders_count = fields.Integer(compute='_compute_counts')
    rfqs_count = fields.Integer(compute='_compute_counts')
    partners_count = fields.Integer(compute='_compute_counts')
    invoices_count = fields.Integer(compute='_compute_counts')

    @api.depends('from_user_id')
    def _compute_module_availability(self):
        env = self.env
        has_helpdesk = 'helpdesk.ticket' in env
        has_crm = 'crm.lead' in env
        has_calendar = 'calendar.event' in env
        has_project = 'project.task' in env
        has_sale = 'sale.order' in env
        has_purchase = 'purchase.order' in env
        has_account = 'account.move' in env
        for wiz in self:
            wiz.has_helpdesk = has_helpdesk
            wiz.has_crm = has_crm
            wiz.has_calendar = has_calendar
            wiz.has_project = has_project
            wiz.has_sale = has_sale
            wiz.has_purchase = has_purchase
            wiz.has_account = has_account

    @api.depends('from_user_id', 'only_future')
    def _compute_counts(self):
        for wiz in self:
            if not wiz.from_user_id:
                wiz.activities_count = 0
                wiz.leads_count = 0
                wiz.calendar_count = 0
                wiz.helpdesk_count = 0
                wiz.tasks_count = 0
                wiz.sale_orders_count = 0
                wiz.rfqs_count = 0
                wiz.partners_count = 0
                wiz.invoices_count = 0
                continue
            wiz.activities_count = len(wiz._get_activities())
            wiz.leads_count = len(wiz._get_leads())
            wiz.calendar_count = len(wiz._get_calendar_events())
            wiz.helpdesk_count = len(wiz._get_helpdesk_tickets())
            wiz.tasks_count = len(wiz._get_tasks())
            wiz.sale_orders_count = len(wiz._get_sale_orders())
            wiz.rfqs_count = len(wiz._get_rfqs())
            wiz.partners_count = len(wiz._get_partners())
            wiz.invoices_count = len(wiz._get_invoices())

    # ------------------------------------------------------------------
    # Record collectors — each returns a recordset (possibly empty)
    # ------------------------------------------------------------------
    def _get_activities(self):
        # Every active (non-Done) activity due today or later, across every
        # model, that the leaver is connected to — assigned to them OR created
        # by them. Done activities are archived (active=False), so the default
        # active_test=True excludes them.
        self.ensure_one()
        leaver = self.from_user_id.id
        Activity = self.env['mail.activity'].sudo()
        return Activity.search([
            ('date_deadline', '>=', fields.Date.context_today(self)),
            '|', ('user_id', '=', leaver), ('create_uid', '=', leaver),
        ])

    def _get_leads(self):
        self.ensure_one()
        if 'crm.lead' not in self.env:
            return self.env['mail.activity']  # empty recordset placeholder unused
        Lead = self.env['crm.lead'].sudo().with_context(active_test=False)
        domain = [('user_id', '=', self.from_user_id.id), ('active', '=', True)]
        if self.only_future:
            # Skip won (probability = 100) and lost (active=False already handled, but
            # also exclude leads sitting in a stage flagged is_won).
            domain += [('probability', '<', 100)]
        return Lead.search(domain)

    def _get_calendar_events(self):
        self.ensure_one()
        if 'calendar.event' not in self.env:
            return self.env['mail.activity']
        Event = self.env['calendar.event'].sudo()
        domain = [('user_id', '=', self.from_user_id.id)]
        if self.only_future:
            domain += [('stop', '>=', fields.Datetime.now())]
        return Event.search(domain)

    def _get_helpdesk_tickets(self):
        self.ensure_one()
        if 'helpdesk.ticket' not in self.env:
            return self.env['mail.activity']
        Ticket = self.env['helpdesk.ticket'].sudo()
        domain = [('user_id', '=', self.from_user_id.id)]
        if self.only_future:
            domain += [('close_date', '=', False)]
        return Ticket.search(domain)

    def _get_tasks(self):
        self.ensure_one()
        if 'project.task' not in self.env:
            return self.env['mail.activity']
        Task = self.env['project.task'].sudo()
        domain = [('user_ids', 'in', self.from_user_id.id), ('active', '=', True)]
        if self.only_future:
            domain += [('state', 'not in', ('1_done', '1_canceled'))]
        return Task.search(domain)

    def _get_sale_orders(self):
        # Quotations only (draft/sent) the leaver is connected to — assigned
        # as salesperson OR created by them. Confirmed/done/cancelled orders
        # stay put: their lifecycle is settled and re-owning them risks
        # disturbing another rep's pipeline.
        self.ensure_one()
        if 'sale.order' not in self.env:
            return self.env['mail.activity']
        leaver = self.from_user_id.id
        SaleOrder = self.env['sale.order'].sudo()
        return SaleOrder.search([
            ('state', 'in', ('draft', 'sent')),
            '|', ('user_id', '=', leaver), ('create_uid', '=', leaver),
        ])

    def _get_rfqs(self):
        # Requests for Quotation only (purchase.order in draft/sent) where the
        # leaver is the buyer/purchase rep (user_id) or the creator. Confirmed
        # purchase orders ('purchase'/'done'/'cancel') stay put — the supplier
        # commitment is settled and re-owning them risks disturbing the active
        # buyer's pipeline.
        self.ensure_one()
        if 'purchase.order' not in self.env:
            return self.env['mail.activity']
        leaver = self.from_user_id.id
        Purchase = self.env['purchase.order'].sudo()
        return Purchase.search([
            ('state', 'in', ('draft', 'sent')),
            '|', ('user_id', '=', leaver), ('create_uid', '=', leaver),
        ])

    def _get_partners(self):
        self.ensure_one()
        Partner = self.env['res.partner'].sudo()
        return Partner.search([('user_id', '=', self.from_user_id.id)])

    def _get_invoices(self):
        # Non-posted customer invoices/refunds/receipts (state='draft') where
        # the leaver is the Salesperson or the creator. Posted/cancelled moves
        # are settled and stay put.
        self.ensure_one()
        if 'account.move' not in self.env:
            return self.env['mail.activity']
        leaver = self.from_user_id.id
        Move = self.env['account.move'].sudo()
        return Move.search([
            ('move_type', 'in', ('out_invoice', 'out_refund', 'out_receipt')),
            ('state', '=', 'draft'),
            '|', ('invoice_user_id', '=', leaver), ('create_uid', '=', leaver),
        ])

    # ------------------------------------------------------------------
    # Main action
    # ------------------------------------------------------------------
    def action_reassign(self):
        self.ensure_one()
        if self.from_user_id == self.to_user_id:
            raise UserError(_("Source and target users must be different."))

        summary = []

        if self.reassign_activities:
            activities = self._get_activities()
            if activities:
                remark = self._build_activity_remark()
                for activity in activities:
                    existing = Markup(activity.note) if activity.note else Markup("")
                    activity.write({
                        'user_id': self.to_user_id.id,
                        'note': remark + existing,
                    })
                summary.append(_("%s activities", len(activities)))

        if self.reassign_leads and self.has_crm:
            leads = self._get_leads()
            if leads:
                leads.write({'user_id': self.to_user_id.id})
                self._post_message(leads, _("Leads reassigned to %s.", self.to_user_id.name))
                summary.append(_("%s leads", len(leads)))

        if self.reassign_calendar and self.has_calendar:
            events = self._get_calendar_events()
            if events:
                events.write({'user_id': self.to_user_id.id})
                self._update_calendar_attendees(events)
                summary.append(_("%s calendar events", len(events)))

        if self.reassign_helpdesk and self.has_helpdesk:
            tickets = self._get_helpdesk_tickets()
            if tickets:
                tickets.write({'user_id': self.to_user_id.id})
                self._post_message(tickets, _("Ticket reassigned to %s.", self.to_user_id.name))
                summary.append(_("%s helpdesk tickets", len(tickets)))

        if self.reassign_tasks and self.has_project:
            tasks = self._get_tasks()
            if tasks:
                for task in tasks:
                    new_users = (task.user_ids - self.from_user_id) | self.to_user_id
                    task.write({'user_ids': [(6, 0, new_users.ids)]})
                self._post_message(tasks, _("Task reassigned to %s.", self.to_user_id.name))
                summary.append(_("%s tasks", len(tasks)))

        if self.reassign_sale_orders and self.has_sale:
            orders = self._get_sale_orders()
            if orders:
                orders.write({'user_id': self.to_user_id.id})
                self._post_message(orders, _("Quotation reassigned to %s.", self.to_user_id.name))
                summary.append(_("%s quotations", len(orders)))

        if self.reassign_rfqs and self.has_purchase:
            rfqs = self._get_rfqs()
            if rfqs:
                rfqs.write({'user_id': self.to_user_id.id})
                self._post_message(rfqs, _("RFQ reassigned to %s.", self.to_user_id.name))
                summary.append(_("%s RFQs", len(rfqs)))

        if self.reassign_partners:
            partners = self._get_partners()
            if partners:
                partners.write({'user_id': self.to_user_id.id})
                self._post_message(
                    partners,
                    _("Salesperson reassigned to %s.", self.to_user_id.name),
                )
                summary.append(_("%s customers", len(partners)))

        if self.reassign_invoices and self.has_account:
            invoices = self._get_invoices()
            if invoices:
                invoices.write({'invoice_user_id': self.to_user_id.id})
                self._post_message(
                    invoices,
                    _("Invoice salesperson reassigned to %s.", self.to_user_id.name),
                )
                summary.append(_("%s invoices", len(invoices)))

        if not summary:
            raise UserError(_("Nothing to reassign for %s with the current options.", self.from_user_id.name))

        message = _("Reassigned from %(from)s to %(to)s: %(items)s.", **{
            'from': self.from_user_id.name,
            'to': self.to_user_id.name,
            'items': ", ".join(summary),
        })
        _logger.info("[era_emp_reassign_activities] %s", message)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Reassignment Done"),
                'message': message,
                'sticky': False,
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _build_activity_remark(self):
        # Markup % auto-escapes substituted values, so a user name containing
        # "<script>" cannot inject HTML into the note.
        self.ensure_one()
        text = _(
            "Transferred from %s on %s",
            self.from_user_id.name or '',
            fields.Date.context_today(self),
        )
        return Markup("<p><i>%s</i></p>") % text

    def _post_message(self, records, body):
        for rec in records:
            try:
                rec.message_post(body=body)
            except Exception:
                _logger.exception(
                    "Failed to post reassignment message on %s(%s)", rec._name, rec.id
                )

    def _update_calendar_attendees(self, events):
        """Replace the source user with the target as event attendee/partner."""
        from_partner = self.from_user_id.partner_id
        to_partner = self.to_user_id.partner_id
        if not from_partner or not to_partner:
            return
        for event in events:
            partners = event.partner_ids
            if from_partner in partners:
                new_partners = (partners - from_partner) | to_partner
                event.write({'partner_ids': [(6, 0, new_partners.ids)]})

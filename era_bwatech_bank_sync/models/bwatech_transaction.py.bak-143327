import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BwatechTransaction(models.Model):
    _name = "bwatech.transaction"
    _description = "BWATECH Transaction"
    _order = "sequence_number desc, id desc"

    connection_id = fields.Many2one("bwatech.connection", required=True, ondelete="cascade", index=True)
    bank_account_id = fields.Many2one("bwatech.bank.account", required=True, ondelete="cascade", index=True)

    sequence_number = fields.Integer(required=True, index=True)
    bank_code = fields.Char()
    account_number = fields.Char()
    amount = fields.Float(
        digits=(18, 3),
        help="Signed as BWATECH sends it: positive is money in, negative is money out.",
    )
    value_date = fields.Datetime()
    entry_date = fields.Datetime()

    transaction_type_code = fields.Char()
    transaction_type_description = fields.Char()
    bank_reference = fields.Char(index=True)
    customer_reference = fields.Char()
    transaction_description = fields.Char()
    third_party_name = fields.Char()
    third_party_bank_code = fields.Char()
    third_party_account_number = fields.Char()

    raw_payload = fields.Text(readonly=True)

    statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        readonly=True,
        copy=False,
        index=True,
        ondelete="set null",
        string="Bank Statement Line",
        help="The Odoo bank statement line created from this transaction.",
    )
    posted_to_odoo = fields.Boolean(
        compute="_compute_posted_to_odoo",
        store=True,
        readonly=True,
        help="Derived from the statement line, so deleting that line makes the "
             "transaction available for posting again.",
    )

    _uniq_bwatech_sequence = models.Constraint(
        "unique(connection_id, bank_account_id, sequence_number)",
        "This BWATECH transaction sequence already exists for this bank account.",
    )

    @api.depends("statement_line_id")
    def _compute_posted_to_odoo(self):
        for transaction in self:
            transaction.posted_to_odoo = bool(transaction.statement_line_id)

    # ------------------------------------------------------------------
    # Posting to the bank journal
    # ------------------------------------------------------------------

    def _posting_blocker(self):
        """Return the reason this transaction cannot be posted, or False.

        Checked before anything is written, because a statement line is posted
        to the ledger the moment it is created and undoing it is expensive.
        """
        self.ensure_one()
        account = self.bank_account_id
        journal = account.journal_id

        if self.posted_to_odoo:
            return _("الحركة مُرحَّلة بالفعل إلى كشف الحساب.")
        if not journal:
            return _(
                "لم تُحدَّد يومية بنكية للحساب %s.\n"
                "افتحي الحساب واختاري اليومية المقابلة له في أودو."
            ) % account.display_account
        if not journal.suspense_account_id:
            return _(
                "اليومية %s بلا حساب معلّق.\n"
                "أودو يرفض إنشاء سطر كشف حساب بدونه — اضبطيه من إعدادات اليومية."
            ) % journal.display_name
        if not (self.value_date or self.entry_date):
            return _("الحركة بلا تاريخ قيمة ولا تاريخ إدخال، فلا يمكن تأريخ القيد.")

        journal_currency = journal.currency_id or journal.company_id.currency_id
        if self.bank_account_id.currency_id and self.bank_account_id.currency_id != journal_currency:
            return _(
                "عملة الحساب لدى بواتك (%(bank)s) تختلف عن عملة اليومية (%(journal)s).\n"
                "وحّدي العملتين قبل الترحيل حتى لا تُسجَّل مبالغ بعملة خاطئة."
            ) % {
                "bank": self.bank_account_id.currency_id.name,
                "journal": journal_currency.name,
            }
        return False

    def _prepare_statement_line_vals(self):
        """Map one BWATECH transaction onto an Odoo bank statement line."""
        self.ensure_one()
        try:
            details = json.loads(self.raw_payload) if self.raw_payload else {}
        except ValueError:
            details = {}

        label = (
            self.transaction_description
            or self.transaction_type_description
            or self.bank_reference
            or _("حركة بواتك %s") % self.sequence_number
        )
        return {
            "journal_id": self.bank_account_id.journal_id.id,
            "date": (self.value_date or self.entry_date).date(),
            "payment_ref": label,
            # The sign comes straight from BWATECH and matches Odoo's own
            # convention: positive is money in, negative is money out.
            "amount": self.amount,
            "partner_name": self.third_party_name or False,
            "account_number": self.third_party_account_number or False,
            "ref": self.customer_reference or self.bank_reference or False,
            "transaction_type": self.transaction_type_description or False,
            "transaction_details": details,
        }

    def action_post_to_odoo(self):
        """Create the bank statement lines for the selected transactions.

        Every transaction is validated first and the lines are created in one
        batch afterwards, so a single unpostable record cannot leave the run
        half finished.

        Creating a statement line makes Odoo post the journal entry straight
        away, which is why nothing is written until every check has passed.
        """
        postable = self.filtered(lambda t: not t.posted_to_odoo).sorted("sequence_number")
        if not postable:
            raise UserError(_("لا توجد حركات قابلة للترحيل ضمن ما اخترتِه."))

        blocked = []
        vals_list = []
        # A plain list, not a recordset: the created lines are matched back to
        # their transactions by position, so the order has to be guaranteed.
        to_link = []
        for transaction in postable:
            blocker = transaction._posting_blocker()
            if blocker:
                blocked.append("• %s" % blocker)
                continue
            vals_list.append(transaction._prepare_statement_line_vals())
            to_link.append(transaction)

        if not vals_list:
            raise UserError(
                _("تعذّر ترحيل أي حركة:\n\n%s") % "\n".join(dict.fromkeys(blocked))
            )

        lines = self.env["account.bank.statement.line"].create(vals_list)
        for transaction, line in zip(to_link, lines):
            transaction.statement_line_id = line.id

        _logger.info("BWATECH: posted %d transaction(s) to the bank journal.", len(lines))
        return self._posting_notification(len(lines), blocked)

    def _posting_notification(self, posted, blocked):
        """Report the outcome without discarding the lines already created."""
        if blocked:
            level = "warning"
            message = _(
                "رُحِّلت %(ok)s حركة، وتعذّر ترحيل %(ko)s:\n\n%(why)s"
            ) % {"ok": posted, "ko": len(blocked), "why": "\n".join(dict.fromkeys(blocked))}
        else:
            level = "success"
            message = _("رُحِّلت %s حركة إلى كشف الحساب البنكي.") % posted
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("BWATECH"),
                "message": message,
                "type": level,
                "sticky": bool(blocked),
            },
        }

    def action_open_statement_line(self):
        self.ensure_one()
        if not self.statement_line_id:
            raise UserError(_("لم تُرحَّل هذه الحركة بعد."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.bank.statement.line",
            "res_id": self.statement_line_id.id,
            "view_mode": "form",
            "target": "current",
            "name": _("Bank Statement Line"),
        }

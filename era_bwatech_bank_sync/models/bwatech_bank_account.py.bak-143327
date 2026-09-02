import json
import logging
from datetime import datetime, timezone

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# BWATECH caps one Transactions Inquiry page at 100 records (spec 2.2.4).
TRANSACTION_PAGE_SIZE = 100
# A single synchronization walks at most this many pages. Nothing is lost when
# the cap is reached: the cursor is persisted after every page, so the next run
# resumes exactly where this one stopped.
MAX_TRANSACTION_PAGES = 50


class BwatechBankAccount(models.Model):
    _name = "bwatech.bank.account"
    _description = "BWATECH Bank Account"
    _rec_name = "display_account"

    connection_id = fields.Many2one("bwatech.connection", required=True, ondelete="cascade", index=True)
    active = fields.Boolean(default=True)

    account_name = fields.Char()
    iban = fields.Char(index=True)
    bban = fields.Char(index=True)
    bank_name = fields.Char()
    bank_code = fields.Char(index=True)
    currency_code = fields.Char()
    entity_name = fields.Char()

    display_account = fields.Char(compute="_compute_display_account", store=True)

    balance = fields.Monetary(currency_field="currency_id", readonly=True)
    currency_id = fields.Many2one("res.currency", compute="_compute_currency", store=True)
    balance_synced_at = fields.Datetime(readonly=True)

    journal_id = fields.Many2one(
        "account.journal",
        domain="[('type', '=', 'bank')]",
        help="Target Odoo bank journal. Transaction posting is intentionally disabled until debit/credit sign mapping is confirmed with BWATECH.",
    )
    sync_transactions = fields.Boolean(default=True)
    last_sequence_number = fields.Integer(
        default=0,
        help="Last BWATECH transaction sequence imported for this account.",
    )
    transaction_ids = fields.One2many("bwatech.transaction", "bank_account_id")
    company_id = fields.Many2one(
        "res.company",
        related="journal_id.company_id",
        store=True,
        readonly=True,
        help="Taken from the journal, which is what Odoo stamps on the statement "
             "line and what the auto-reconciliation cron filters on.",
    )
    auto_post = fields.Boolean(
        string="Post Automatically",
        default=False,
        help="Post new transactions to the bank journal right after each "
             "synchronization. Off by default: a statement line lands in the "
             "ledger as a posted entry, so this is switched on only once the "
             "amount signs have been verified against real data.",
    )
    unposted_count = fields.Integer(compute="_compute_unposted_count")

    _uniq_connection_iban = models.Constraint(
        "unique(connection_id, iban)",
        "The IBAN must be unique per BWATECH connection.",
    )

    @api.depends("account_name", "iban", "bban")
    def _compute_display_account(self):
        for rec in self:
            number = rec.iban or rec.bban or ""
            rec.display_account = f"{rec.account_name or 'Bank Account'} - {number}"

    @api.depends("currency_code")
    def _compute_currency(self):
        Currency = self.env["res.currency"]
        for rec in self:
            rec.currency_id = Currency.search([("name", "=", rec.currency_code)], limit=1)

    def _compute_unposted_count(self):
        counts = dict(self.env["bwatech.transaction"]._read_group(
            [("bank_account_id", "in", self.ids), ("posted_to_odoo", "=", False)],
            ["bank_account_id"], ["__count"],
        ))
        for account in self:
            account.unposted_count = counts.get(account, 0)

    def action_post_transactions(self):
        """Post every transaction of these accounts that is still unposted."""
        pending = self.env["bwatech.transaction"].search([
            ("bank_account_id", "in", self.ids),
            ("posted_to_odoo", "=", False),
        ], order="sequence_number")
        if not pending:
            raise UserError(_("لا توجد حركات غير مُرحَّلة على هذا الحساب."))
        return pending.action_post_to_odoo()

    def action_open_transactions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("BWATECH Transactions"),
            "res_model": "bwatech.transaction",
            "view_mode": "list,form",
            "domain": [("bank_account_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_sync_balance(self):
        for account in self:
            account_number = account.iban or account.bban
            if not account_number:
                raise UserError(_("BWATECH account has no IBAN/BBAN."))

            req = {"accountNumber": account_number}
            if not account.iban and account.bank_code:
                req["bankCode"] = account.bank_code

            data = account.connection_id._post_cm(
                "/V1/Account/GetBalance",
                {"getBalanceRequest": req},
                service="GetBalance",
            )
            result = data.get("getBalanceResponse") or {}
            currency_code = result.get("accountCurrency") or account.currency_code
            account.write({
                "balance": float(result.get("balance") or 0.0),
                "currency_code": currency_code,
                "balance_synced_at": fields.Datetime.now(),
            })
        return True

    def action_sync_transactions(self):
        """Pull every new transaction, walking BWATECH's pages until exhausted.

        BWATECH returns at most ``pageSize`` records per call and reports no
        total count, so the end of the data is detected by a page that comes
        back shorter than requested.

        Two paging modes are used, decided once per account before the loop:

        * **Cursor** — when a sequence has already been stored,
          ``lastSequenceNumber`` asks for everything after it. The
          specification states this overrides every other filter except
          ``pageSize``, so the cursor simply moves to the highest sequence of
          each page.
        * **Page number** — on the very first synchronization there is no
          cursor yet, so ``pageNumber`` is incremented instead.

        Reading a single page and then jumping the stored cursor to that page's
        highest sequence would silently drop every transaction that did not fit
        in it, which is why the loop exists.

        Assumption to confirm with BWATECH: a page answering
        ``lastSequenceNumber`` carries the *oldest* pending records, not the
        newest. The specification does not state an ordering. If it were the
        other way round no cursor could walk the backlog, so the loop warns
        when it sees the symptom instead of failing quietly.
        """
        for account in self:
            account_number = account.iban or account.bban
            if not account_number:
                continue

            use_cursor = bool(account.last_sequence_number)
            cursor = account.last_sequence_number
            page_number = 1
            pages_read = 0
            stored = 0
            warned_stale = False
            previous_page_was_full = False

            while pages_read < MAX_TRANSACTION_PAGES:
                items = account._fetch_transaction_page(
                    account_number,
                    cursor=cursor if use_cursor else 0,
                    page_number=page_number,
                )
                pages_read += 1
                page_length = len(items)

                # A backend that ignores the cursor would otherwise hand back
                # the same records forever. Drop what we already have and say so.
                if use_cursor and cursor:
                    fresh = [
                        item for item in items
                        if int(item.get("sequenceNumber") or 0) > cursor
                    ]
                    if len(fresh) != page_length and not warned_stale:
                        warned_stale = True
                        _logger.warning(
                            "BWATECH returned %d transaction(s) at or below the "
                            "requested sequence %s for account %s. They were "
                            "ignored; confirm the pagination contract with BWATECH.",
                            page_length - len(fresh), cursor, account.display_account,
                        )
                    items = fresh

                if not items:
                    # A full page followed by an empty one is what "BWATECH
                    # returned its newest records instead of its oldest" looks
                    # like from here, and it is indistinguishable from a clean
                    # finish on an exact multiple of the page size. Say so
                    # rather than let a truncated import pass unnoticed.
                    if use_cursor and previous_page_was_full:
                        _logger.warning(
                            "BWATECH: account %s finished on an exactly full page. "
                            "If BWATECH orders a page newest-first, transactions "
                            "below sequence %s were never sent. Confirm the "
                            "ordering of lastSequenceNumber with BWATECH.",
                            account.display_account, cursor,
                        )
                    break

                page_max = account._store_transactions(items)
                stored += len(items)

                if use_cursor and page_max <= cursor:
                    _logger.warning(
                        "BWATECH: sequence did not advance past %s for account %s; "
                        "stopping to avoid requesting the same page indefinitely.",
                        cursor, account.display_account,
                    )
                    break

                cursor = max(cursor, page_max)
                # Persisted after every page so an interrupted run still moves forward.
                account.last_sequence_number = cursor
                page_number += 1
                previous_page_was_full = page_length >= TRANSACTION_PAGE_SIZE

                # A short page means BWATECH has nothing left to give.
                if page_length < TRANSACTION_PAGE_SIZE:
                    break
            else:
                _logger.warning(
                    "BWATECH: stopped after %d pages for account %s. The remaining "
                    "transactions are not lost — the next synchronization resumes "
                    "from sequence %s.",
                    pages_read, account.display_account, cursor,
                )

            if stored:
                _logger.info(
                    "BWATECH: stored %d transaction(s) over %d page(s) for account "
                    "%s; sequence is now %s.",
                    stored, pages_read, account.display_account, cursor,
                )
        return True

    def _fetch_transaction_page(self, account_number, cursor=0, page_number=1):
        """Request a single Transactions Inquiry page (spec 2.2.4)."""
        self.ensure_one()
        request = {
            "accountNumber": account_number,
            "pageSize": TRANSACTION_PAGE_SIZE,
        }
        if self.bank_code:
            request["bankCode"] = self.bank_code
        if cursor:
            request["lastSequenceNumber"] = cursor
        else:
            request["pageNumber"] = page_number

        data = self.connection_id._post_cm(
            "/V1/Transactions/Inquiry",
            {"transactionsInquiryRequest": request},
            service="TransactionsInquiry",
        )
        return (data.get("transactionsInquiryResponse") or {}).get("transactions") or []

    def _store_transactions(self, items):
        """Upsert one page and return the highest sequence number it carried.

        The existing rows are looked up once for the whole page rather than once
        per transaction, because paging multiplies that cost by the page count.
        """
        self.ensure_one()
        Tx = self.env["bwatech.transaction"].sudo()

        by_sequence = {}
        for item in items:
            sequence = int(item.get("sequenceNumber") or 0)
            if not sequence:
                _logger.warning(
                    "BWATECH returned a transaction without sequenceNumber for "
                    "account %s; skipped.", self.display_account,
                )
                continue
            by_sequence[sequence] = item

        if not by_sequence:
            return 0

        existing = Tx.search([
            ("connection_id", "=", self.connection_id.id),
            ("bank_account_id", "=", self.id),
            ("sequence_number", "in", list(by_sequence)),
        ])
        existing_by_sequence = {rec.sequence_number: rec for rec in existing}

        to_create = []
        for sequence, item in by_sequence.items():
            vals = self._transaction_vals(sequence, item)
            record = existing_by_sequence.get(sequence)
            if record:
                record.write(vals)
            else:
                to_create.append(vals)

        if to_create:
            Tx.create(to_create)

        return max(by_sequence)

    def _transaction_vals(self, sequence, item):
        """Map one BWATECH transaction onto the staging model."""
        self.ensure_one()
        third = item.get("thirdPartyInformation") or {}
        return {
            "connection_id": self.connection_id.id,
            "bank_account_id": self.id,
            "sequence_number": sequence,
            "bank_code": item.get("bankCode"),
            "account_number": item.get("accountNumber"),
            # BWATECH sends the sign: positive is money in, negative is money out.
            "amount": float(item.get("amount") or 0.0),
            "value_date": self._parse_bwatech_datetime(item.get("valueDate")),
            "entry_date": self._parse_bwatech_datetime(item.get("entryDate")),
            "transaction_type_code": item.get("transactionTypeCode"),
            "transaction_type_description": item.get("transactionTypeDescription"),
            # The specification spells this "bankReferance"; accept both spellings.
            "bank_reference": item.get("bankReferance") or item.get("bankReference"),
            "customer_reference": item.get("customerReferance") or item.get("customerReference"),
            "transaction_description": item.get("transactionDescription"),
            "third_party_name": third.get("thirdPartyName") or item.get("thirdPartyName"),
            "third_party_bank_code": third.get("thirdPartyBankCode") or item.get("thirdPartyBankCode"),
            "third_party_account_number": third.get("thirdPartyAccountNumber") or item.get("thirdPartyAccountNumber"),
            "raw_payload": json.dumps(item, ensure_ascii=False, indent=2, default=str),
        }

    def _parse_bwatech_datetime(self, value):
        if not value:
            return False
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        if dt.tzinfo is not None:
            # Odoo stores naive datetimes in UTC.
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

from odoo import fields, models


class BwatechTransaction(models.Model):
    _name = "bwatech.transaction"
    _description = "BWATECH Transaction"
    _order = "sequence_number desc, id desc"

    connection_id = fields.Many2one("bwatech.connection", required=True, ondelete="cascade", index=True)
    bank_account_id = fields.Many2one("bwatech.bank.account", required=True, ondelete="cascade", index=True)

    sequence_number = fields.Integer(required=True, index=True)
    bank_code = fields.Char()
    account_number = fields.Char()
    amount = fields.Float(digits=(18, 3))
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
    posted_to_odoo = fields.Boolean(default=False, readonly=True)

    _uniq_bwatech_sequence = models.Constraint(
        "unique(connection_id, bank_account_id, sequence_number)",
        "This BWATECH transaction sequence already exists for this bank account.",
    )

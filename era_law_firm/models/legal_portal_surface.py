"""What the ORM itself will hand a portal user, field by field.

The portal ACL grants read on whole models and the record rules scope rows —
neither can hide a column. /web/dataset/call_kw is plain auth='user', so a
logged-in client can search_read any field on rows the rules admit, template
curation notwithstanding: a refund's staff-written reason, the case outcome
narrative, the hours and expense economics of a fixed-fee matter, who at the
firm handles what.

The fix is the one the module already uses for internal_notes: field-level
groups. base.group_user is every internal user and no portal user, so marking
a field with it removes it from the portal's ORM surface without changing a
thing for staff. Declared in this dedicated file, imported last, so the
incremental definitions only ADD the groups attribute and keep help texts and
labels contributed elsewhere.

What deliberately stays readable: the client's own facts (najiz number and
URL, claim amount, their lawyer's name, hearing dates including hijri) and
aggregates that mirror their own invoices (invoiced/paid/outstanding).
"""
from odoo import fields, models

INTERNAL = 'base.group_user'


class LegalCase(models.Model):
    _inherit = 'legal.case'

    # Free-text staff narrative and the firm's internal economics.
    outcome = fields.Text(groups=INTERNAL)
    billable_hours = fields.Float(groups=INTERNAL)
    expense_amount = fields.Monetary(groups=INTERNAL)
    team_user_ids = fields.Many2many('res.users', groups=INTERNAL)
    confidential = fields.Boolean(groups=INTERNAL)
    # Legacy free-text court fields, superseded by the judiciary records.
    jurisdiction = fields.Char(groups=INTERNAL)
    court = fields.Char(groups=INTERNAL)
    circuit = fields.Char(groups=INTERNAL)


class LegalDocument(models.Model):
    _inherit = 'legal.document'

    owner_id = fields.Many2one('res.users', groups=INTERNAL)
    reviewer_id = fields.Many2one('res.users', groups=INTERNAL)
    restricted = fields.Boolean(groups=INTERNAL)
    allowed_user_ids = fields.Many2many('res.users', groups=INTERNAL)
    najiz_reference = fields.Char(groups=INTERNAL)


class LegalTrustTransaction(models.Model):
    _inherit = 'legal.trust.transaction'

    # Staff free text, and guaranteed present on refunds — the wizard
    # requires both before it will post one.
    reason = fields.Text(groups=INTERNAL)
    reference = fields.Char(groups=INTERNAL)

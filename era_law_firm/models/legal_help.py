"""Field help text, kept in one place.

Declared incrementally: each field is re-stated with nothing but ``help``, so the
original definition keeps its type, domain, groups and everything else. Putting it
on the field rather than in a view means the "?" shows up in every view, in the
portal and in the developer tooltip alike.

The aim is a system that explains itself: say what the field is for and what it
affects, not what its label already says.
"""

from odoo import fields, models


class LegalCase(models.Model):
    _inherit = 'legal.case'

    name = fields.Char(help="Internal reference, issued automatically as LAW/<year>/<number>. Each company has its own counter.")
    najiz_number = fields.Char(help="Case number as issued by Najiz. Entered by hand -- this module does not connect to Najiz. It must be unique within the company.")
    najiz_url = fields.Char(help="Direct link to the case in Najiz, kept for quick reference only.")
    case_type = fields.Selection(help="Determines how the file is handled: a full litigation file, an execution request, a one-off consultation, or something else.")
    jurisdiction = fields.Char(help="The judicial authority the case falls under, for example general courts, commercial courts or the labour circuit.")
    court = fields.Char(help="The court hearing the case, as named in the Najiz record.")
    circuit = fields.Char(help="The specific circuit or bench within the court.")
    client_id = fields.Many2one(help="The party the firm acts for. Changing it voids the conflict check and the case must be screened again.")
    lawyer_id = fields.Many2one(help="The lawyer answerable for the file. They can always open it, and hearing and deadline reminders are raised against them.")
    team_user_ids = fields.Many2many(help="Colleagues who may open this file in addition to the responsible lawyer. Anyone not listed here and not a legal manager cannot see it at all.")
    stage_id = fields.Many2one(help="Where the file stands in the litigation cycle. Stages are configurable and drive the kanban board.")
    state = fields.Selection(help="Draft files are still being prepared. Confirming requires a valid conflict check, and closing requires the client's trust balance to be settled.")
    confidential = fields.Boolean(help="Marks the file as sensitive so staff handle it accordingly. Actual access is enforced by the case team and by the Restricted flag on individual documents, not by this box.")
    claim_amount = fields.Monetary(help="The value claimed in the case. Used for reporting; it does not drive billing.")
    conflict_check_id = fields.Many2one(help="The conflict of interest screening that authorised this file. It is cleared automatically whenever the client or the parties change, and the case then has to be screened again.")
    date_filed = fields.Date(help="Date the case was filed with the court.")
    close_date = fields.Date(help="Set automatically when the case is closed.")
    outcome = fields.Text(help="How the matter ended: the judgment, the settlement terms, or the withdrawal.")
    internal_notes = fields.Html(help="Working notes for lawyers and legal managers. Never shown to the client and never visible in the portal.")
    conflict_state = fields.Selection(help="Result of the latest conflict screening. A blocked result prevents the case from being confirmed.")
    billable_hours = fields.Float(help="Hours recorded on this file that have been marked billable or already invoiced.")
    invoiced_amount = fields.Monetary(help="Total of posted customer invoices linked to this case.")
    paid_amount = fields.Monetary(help="Portion of the invoiced amount that has been settled.")
    outstanding_amount = fields.Monetary(help="Invoiced but still unpaid. This is the figure to chase.")
    expense_amount = fields.Monetary(help="Approved and invoiced disbursements on this file, such as court fees and expert reports.")


class LegalCaseStage(models.Model):
    _inherit = 'legal.case.stage'

    sequence = fields.Integer(help="Order the stage appears in on the kanban board.")
    fold = fields.Boolean(help="Collapse this stage's column on the kanban board. Useful for stages that hold finished files.")
    is_closed = fields.Boolean(help="Marks this stage as an end state for reporting purposes.")
    is_rejected = fields.Boolean(help="Marks this stage as a refusal or dismissal, so it is not counted as a successful close.")
    company_id = fields.Many2one(help="Leave empty to share the stage across every company.")


class LegalCaseParty(models.Model):
    _inherit = 'legal.case.party'

    role = fields.Selection(help="The capacity this party holds in the case. Opponents are what the conflict check screens most closely -- the same person appearing as your client elsewhere is the situation to catch.")
    representative_id = fields.Many2one(help="The person or firm acting for this party, where they are not representing themselves.")
    lawyer_id = fields.Many2one(help="Opposing or accompanying counsel for this party, for the record.")
    portal_visible = fields.Boolean(help="Show this party to the client in the portal. Leave off to keep a party out of the client's view.")


class LegalConflictCheck(models.Model):
    _inherit = 'legal.conflict.check'

    state = fields.Selection(help="Clear means no party of this case appears in another file. Blocked means at least one does and the case cannot be confirmed. Overridden means a legal manager accepted the risk on the record.")
    party_signature = fields.Char(help="Fingerprint of the parties at the moment of screening: normalised names, identity and registration numbers, phone and e-mail. If it stops matching the case, the screening is stale and must be repeated.")
    line_ids = fields.One2many(help="Each other file where a party of this case already appears, and the capacity they held there.")
    override_reason = fields.Text(help="Why the firm may still accept this engagement despite the match. Mandatory, recorded on the case, and attributed to the manager who gave it.")
    approved_by = fields.Many2one(help="The legal manager who overrode the blocked result.")
    approved_at = fields.Datetime(help="When the override was given.")


class LegalConflictCheckLine(models.Model):
    _inherit = 'legal.conflict.check.line'

    source_case_id = fields.Many2one(help="The other file this party already appears in.")
    role = fields.Char(help="The capacity the party held in that other file. Acting against a former or current client is the conflict this screening exists to catch.")


class LegalHearing(models.Model):
    _inherit = 'legal.hearing'

    hijri_date = fields.Char(help="Hijri date as printed by Najiz, kept as free text for cross-checking. Scheduling itself runs on the Gregorian date so that no automatic conversion can shift a court date.")
    lawyer_id = fields.Many2one(help="The lawyer attending. The reminder activity is raised against them.")
    calendar_event_id = fields.Many2one(help="The calendar entry kept in step with this hearing. Created on confirmation and removed if the hearing is cancelled.")
    reminder_scheduled = fields.Boolean(help="Set once the reminder activity has been raised, so the daily job does not raise it twice.")
    state = fields.Selection(help="Confirming a hearing places it on the calendar. Cancelling removes it again.")


class LegalDeadline(models.Model):
    _inherit = 'legal.deadline'

    deadline_date = fields.Date(help="The binding date the firm works to. The lawyer sets this and remains responsible for it -- nothing here sets it automatically.")
    source = fields.Char(help="Where the binding date comes from, for example the date the judgment was served. Recorded so the calculation can be audited later.")
    rule_id = fields.Many2one(help="Optional statutory rule used to propose a date. Assistive only.")
    start_date = fields.Date(help="The date the statutory period runs from, such as service of the judgment.")
    suggested_date = fields.Date(help="Start date plus the rule's period. A suggestion for the lawyer to review -- suspended periods, holidays and protected categories are not calculated.")
    overdue_state = fields.Selection(help="How the deadline stands against today's date. Overdue rows are highlighted in the list.")
    user_id = fields.Many2one(help="Who must act on this deadline. The reminder activity is raised against them.")
    reminder_scheduled = fields.Boolean(help="Set once the reminder activity has been raised, so the daily job does not raise it twice.")


class LegalDeadlineRule(models.Model):
    _inherit = 'legal.deadline.rule'

    days = fields.Integer(help="Length of the statutory period in days, counted from the start point.")
    start_point = fields.Selection(help="What the period runs from: the date of the judgment, the date of service, or a date the lawyer enters.")
    legal_reference = fields.Char(help="The statutory article this period comes from, so the suggestion can be checked against the source.")
    warning = fields.Text(help="Caution shown to whoever uses this rule, for example that the period does not run during suspension of proceedings.")


class LegalDocument(models.Model):
    _inherit = 'legal.document'

    document_type = fields.Selection(help="What kind of document this is. Deeds and powers of attorney are the ones worth tracking an expiry date for.")
    version = fields.Char(help="Version marker for successive drafts of the same document.")
    owner_id = fields.Many2one(help="Who produced the document. They cannot approve it themselves -- review has to come from someone else.")
    reviewer_id = fields.Many2one(help="Who approved or rejected the document. Filled automatically on review.")
    state = fields.Selection(help="Only an approved document can be published to the client portal, and an approved document is archived rather than deleted.")
    restricted = fields.Boolean(help="Hide this document from everyone except its owner, its reviewer, the users listed below and legal managers.")
    allowed_user_ids = fields.Many2many(help="The only colleagues who may open this document while it is restricted.")
    attachment_id = fields.Many2one(help="The stored file itself. Created automatically when you upload through the File field.")
    portal_published = fields.Boolean(help="Whether the client can see and download this document from the portal. Only approved documents can be published.")
    najiz_reference = fields.Char(help="The document's reference in Najiz, where it has one.")
    hijri_date = fields.Char(help="Hijri date as printed on the document, kept as free text for cross-checking.")
    expiry_date = fields.Date(help="When the document stops being valid. Worth setting on powers of attorney so renewal is not missed.")


class LegalEngagement(models.Model):
    _inherit = 'legal.engagement'

    billing_type = fields.Selection(help="How the client is charged: a fixed sum, an hourly rate, staged payments, or a share of what is recovered.")
    hourly_rate = fields.Monetary(help="Rate applied to time recorded against this engagement.")
    amount = fields.Monetary(help="Agreed fee for a fixed-price engagement.")
    product_id = fields.Many2one(help="The service line used on the invoice, which also determines the income account and the tax applied.")
    state = fields.Selection(help="Nothing can be invoiced against an engagement until it is active.")


class LegalEngagementMilestone(models.Model):
    _inherit = 'legal.engagement.milestone'

    due_date = fields.Date(help="When this staged payment is expected to fall due.")
    amount = fields.Monetary(help="Value of this stage of the engagement.")
    state = fields.Selection(help="Only milestones marked ready are offered by the invoice wizard.")
    invoice_line_id = fields.Many2one(help="The invoice line this milestone was billed on. Its presence is what prevents it being billed twice.")


class LegalSuccessFee(models.Model):
    _inherit = 'legal.success.fee'

    amount = fields.Monetary(help="The fee claimed on the successful outcome.")
    evidence = fields.Text(help="The judgment, settlement or recovery the fee is earned on. Required, because a success fee has to be justifiable after the fact.")
    state = fields.Selection(help="A success fee has to be approved by a legal manager before it can be invoiced.")
    invoice_line_id = fields.Many2one(help="The invoice line this fee was billed on. Its presence is what prevents it being billed twice.")


class LegalTimeEntry(models.Model):
    _inherit = 'legal.time.entry'

    hours = fields.Float(help="Time spent, in hours. Must be greater than zero.")
    rate = fields.Monetary(help="Rate applied to this entry. Defaults from the engagement but can be adjusted per entry.")
    amount = fields.Monetary(help="Hours multiplied by rate.")
    state = fields.Selection(help="Only entries marked billable are offered by the invoice wizard. Draft time is recorded but not charged.")
    invoice_line_id = fields.Many2one(help="The invoice line this entry was billed on. Its presence is what prevents it being billed twice.")


class LegalExpense(models.Model):
    _inherit = 'legal.expense'

    amount = fields.Monetary(help="Amount disbursed on the client's behalf, such as a court fee or an expert's report.")
    product_id = fields.Many2one(help="The expense line used on the invoice, which also determines the account and the tax applied.")
    state = fields.Selection(help="Only approved expenses are offered by the invoice wizard.")
    invoice_line_id = fields.Many2one(help="The invoice line this expense was billed on. Its presence is what prevents it being billed twice.")


class LegalTrustAccount(models.Model):
    _inherit = 'legal.trust.account'

    partner_id = fields.Many2one(help="The client whose money this account holds. One account per client per company.")
    posted_balance = fields.Monetary(help="Sum of every posted movement on this account.")
    available_balance = fields.Monetary(help="What can still be drawn on. A withdrawal that would take this below zero is refused.")
    state = fields.Selection(help="Freezing stops all movement. An account can only be closed once its balance is nil, which is why a surplus has to be refunded first.")


class LegalTrustTransaction(models.Model):
    _inherit = 'legal.trust.transaction'

    transaction_type = fields.Selection(help="Deposit takes client money in as a liability. Apply settles a posted invoice from the balance -- this is the moment client money becomes firm revenue. Refund returns the surplus. Transfer reallocates between the client's own cases.")
    amount = fields.Monetary(help="Always entered as a positive figure. The direction comes from the movement type.")
    signed_amount = fields.Monetary(help="The amount as it affects the balance: positive for a deposit, negative for a withdrawal.")
    invoice_id = fields.Many2one(help="The posted invoice this movement settles. It must belong to the same client, and it is reconciled against the trust entry.")
    move_id = fields.Many2one(help="The journal entry this movement posted. Client money is booked to a segregated liability account, never to revenue.")
    reversal_move_id = fields.Many2one(help="The reversing entry raised when a posted movement is cancelled. Posted movements are reversed, never deleted, so the audit trail survives.")
    reversed_transaction_id = fields.Many2one(help="The movement this one reverses.")
    reference = fields.Char(help="Bank or payment reference for the movement. Mandatory when refunding client money.")
    reason = fields.Text(help="Why the movement was made. Mandatory for refunds.")
    case_id = fields.Many2one(help="Optional: the case this movement relates to, where the client has more than one file.")
    state = fields.Selection(help="Only posted movements affect the balance.")


class LegalAuditLog(models.Model):
    _inherit = 'legal.audit.log'

    operation = fields.Char(help="What was done, for example posting a trust movement or dispatching an AI request.")
    changed_fields = fields.Text(help="Which fields were involved. Identity numbers and secrets are deliberately never recorded here.")
    fingerprint = fields.Char(help="Hash over the record, operation and timestamp, so a tampered entry can be detected. Entries cannot be edited or deleted at all.")


class LegalConsultation(models.Model):
    _inherit = 'legal.consultation'

    partner_id = fields.Many2one(help="The person seeking advice. They do not have to be an existing client.")
    notes = fields.Html(help="What was discussed. Visible to lawyers and legal managers only.")
    case_id = fields.Many2one(help="The case this consultation became, if it was converted. A consultation can only be converted once.")
    state = fields.Selection(help="Converting a consultation opens a case at the intake stage and links the two.")


class ResCompany(models.Model):
    _inherit = 'res.company'

    legal_default_city = fields.Char(help="Pre-filled as the city on new cases.")
    legal_hearing_reminder_days = fields.Integer(help="How many days before a hearing the reminder activity is raised.")
    legal_trust_journal_id = fields.Many2one(help="Journal every client trust movement is posted to, kept separate from the firm's own entries. Filled automatically on install if left empty.")
    legal_trust_liability_account_id = fields.Many2one(help="Where client money sits while the firm holds it. It is a liability, not firm revenue -- that is the whole point of segregating it.")
    legal_trust_bank_account_id = fields.Many2one(help="The segregated bank account client money is deposited into and refunded from.")
    legal_trust_receivable_account_id = fields.Many2one(help="Must be the same receivable the firm's invoices use: applying trust funds reconciles against the invoice's receivable line, and reconciliation only works within one account.")


class ResPartner(models.Model):
    _inherit = 'res.partner'

    legal_identity_number = fields.Char(help="National ID or Iqama number. Visible to legal managers only, and never sent to an AI provider.")
    legal_registration_number = fields.Char(help="Commercial registration number for a corporate party. Visible to legal managers only.")

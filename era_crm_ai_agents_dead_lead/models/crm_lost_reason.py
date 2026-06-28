# -*- coding: utf-8 -*-
"""Classification metadata on lost reasons (manager-editable).

The Dead-Lead agent classifies a lost lead by its lost reason. Per the
No-Hardcoded-Policy rule (which names "loss-reason buckets" as an editable
list), the reason -> bucket mapping and the "is this reason worth resurrecting?"
eligibility are POLICY: they live as manager-editable fields on crm.lost.reason,
never as constants in code. The view that exposes them is added in task 3.8.
"""
from odoo import fields, models

# The reason-bucket vocabulary consumed by the drafting engine (3.4). This tuple
# is a stable TECHNICAL taxonomy (like the suite's other Selections); the
# manager-editable POLICY is which reason maps to which bucket (the field below)
# and whether a reason is resurrectable. Promote to a full editable model later
# if buckets themselves ever need to be manager-defined.
REASON_BUCKET_SELECTION = [
    ("price", "Price / Budget"),
    ("timing", "Timing"),
    ("competitor", "Competitor"),
    ("no_response", "No Response"),
    ("requirements", "Requirements Mismatch"),
    ("other", "Other"),
]


class CrmLostReason(models.Model):
    _inherit = "crm.lost.reason"

    crm_ai_reason_bucket = fields.Selection(
        selection=REASON_BUCKET_SELECTION,
        string="Dead-Lead Bucket",
        help="How the Dead-Lead Resurrection agent classifies leads lost for "
             "this reason; drives the comeback approach (task 3.4). Unset is "
             "treated as 'Other'.")
    crm_ai_resurrectable = fields.Boolean(
        string="Eligible for Resurrection", default=True,
        help="If off, leads lost for this reason are NEVER resurrected by the "
             "Dead-Lead agent (e.g. 'Not a real prospect' / 'Do not contact'). "
             "Defaults on, so existing behaviour is preserved until a manager "
             "deliberately opts a reason out.")

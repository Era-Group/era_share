# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class CrmAiConsent(models.Model):
    """Append-only PDPL consent log: one row per consent event (grant /
    withdrawal) per contact and consent type. The *current* consent state for a
    (partner, type) pair is the most recent row; history is never edited, so the
    log doubles as the legal audit trail PDPL requires.

    Runs entirely under the calling user's permissions (Rule 09 / 19) — no sudo.
    The only elevation in play is the Base audit log's own create-only sudo,
    reached through ``crm.ai.audit.log.log()``.
    """

    _name = "crm.ai.consent"
    _description = "CRM AI PDPL Consent Log"
    # Latest event first — has_consent() and the views rely on this ordering.
    _order = "create_date desc, id desc"

    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Contact",
        # Not required at field level so DSAR erasure can sever the PII link
        # (see _check_partner: a row may only be partner-less once erased).
        ondelete="cascade",
        index=True,
    )
    consent_type = fields.Selection(
        selection=[
            ("marketing", "Marketing"),
            ("service", "Service"),
        ],
        string="Consent Type",
        required=True,
        default="marketing",
        index=True,
    )
    state = fields.Selection(
        selection=[
            ("none", "None"),
            ("granted", "Granted"),
            ("withdrawn", "Withdrawn"),
        ],
        string="State",
        required=True,
        default="none",
        index=True,
    )
    granted_on = fields.Datetime(string="Granted On")
    source = fields.Char(
        string="Source",
        help="Where this consent event originated (e.g. web form, phone, import).",
    )
    opt_out_on = fields.Datetime(string="Opted Out On")
    erased = fields.Boolean(
        string="Erased (DSAR)",
        default=False,
        help="Set when a DSAR erasure has anonymized this row by severing its "
             "contact link. The row is kept only as an anonymized event count.",
    )

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("partner_id", "erased")
    def _check_partner(self):
        for rec in self:
            if not rec.partner_id and not rec.erased:
                raise ValidationError(_(
                    "A consent record must reference a contact unless it has "
                    "been erased under a DSAR request."
                ))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @api.model
    def _as_partner(self, partner):
        """Accept a res.partner record or a bare id; return a record."""
        if hasattr(partner, "_name"):
            return partner
        return self.env["res.partner"].browse(int(partner))

    # ------------------------------------------------------------------
    # Public API (consumed by guard() in task 1.5 and opt-out in 1.6)
    # ------------------------------------------------------------------
    @api.model
    def has_consent(self, partner, consent_type="marketing"):
        """Return True only if the *latest* consent event for this contact and
        type is 'granted'. None / withdrawn / no record at all → False.

        Erased rows have no partner_id, so they never satisfy the domain — an
        erased contact correctly reads as having no consent.
        """
        partner_rec = self._as_partner(partner)
        if not partner_rec:
            return False
        latest = self.search(
            [
                ("partner_id", "=", partner_rec.id),
                ("consent_type", "=", consent_type),
            ],
            order="create_date desc, id desc",
            limit=1,
        )
        return bool(latest) and latest.state == "granted"

    @api.model
    def register_consent(self, partner, consent_type="marketing",
                         state="granted", source=None, granted_on=None):
        """Append one consent event and return it. Stamps granted_on on a grant
        and opt_out_on on a withdrawal. Withdrawals are logged to the Base
        critical audit log (loss of consent is a sensitive PDPL decision)."""
        partner_rec = self._as_partner(partner)
        vals = {
            "partner_id": partner_rec.id,
            "consent_type": consent_type,
            "state": state,
            "source": source,
        }
        if state == "granted":
            vals["granted_on"] = granted_on or fields.Datetime.now()
        elif state == "withdrawn":
            vals["opt_out_on"] = fields.Datetime.now()

        record = self.create(vals)

        if state == "withdrawn":
            self.env["crm.ai.audit.log"].log(
                "other",
                record=partner_rec,
                after={
                    "event": "consent_withdrawn",
                    "consent_type": consent_type,
                    "source": source,
                },
            )
        return record

    @api.model
    def handle_dsar(self, partner, kind):
        """Handle a PDPL Data Subject Access Request.

        kind='access'  → return the contact's full consent history as a list of
                          dicts (the data we hold on them). Logged.
        kind='erasure' → anonymize every consent row for the contact: sever the
                          partner link and clear the free-text source, keeping
                          the row (marked erased) only as an anonymized count.
                          Logged as a 'delete' event BEFORE the PII is severed.
        """
        if kind not in ("access", "erasure"):
            raise ValidationError(_(
                "Unknown DSAR kind %r — expected 'access' or 'erasure'."
            ) % (kind,))

        partner_rec = self._as_partner(partner)
        records = self.search([("partner_id", "=", partner_rec.id)])

        if kind == "access":
            export = [
                {
                    "consent_type": r.consent_type,
                    "state": r.state,
                    "granted_on": fields.Datetime.to_string(r.granted_on),
                    "opt_out_on": fields.Datetime.to_string(r.opt_out_on),
                    "source": r.source,
                }
                for r in records
            ]
            self.env["crm.ai.audit.log"].log(
                "other",
                record=partner_rec,
                after={"event": "dsar_access", "records": len(export)},
            )
            return export

        # kind == "erasure": log first (while we still have the link), then
        # apply the configured erasure mode (anonymize by default, or hard delete).
        from ..services.compliance_config import ComplianceConfig
        mode = ComplianceConfig(self.env).s("dsar_erasure_mode") or "anonymize"
        count = len(records)
        self.env["crm.ai.audit.log"].log(
            "delete",
            record=partner_rec,
            before={"event": "dsar_erasure", "mode": mode, "records": count},
            after={"event": "dsar_erasure_done", "mode": mode, "records": count},
        )
        if mode == "delete":
            records.unlink()
        else:
            records.write({
                "partner_id": False,
                "source": False,
                "erased": True,
            })
        return count

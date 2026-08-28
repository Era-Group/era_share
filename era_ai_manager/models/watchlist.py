import ast
import logging
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Relative dates for domains. A watchlist is evaluated every day for months, so
# a literal date in it is a bug with a delay on it: "registered more than two
# months ago" written as registered_at < '2026-06-28' silently becomes "before
# a fixed day in the past" and the audience drains away. The domain is stored
# as data and read with literal_eval, which cannot compute, so these tokens are
# substituted at evaluation time instead.
RELATIVE_DATE = re.compile(r"^\{\{(days_ago|days_ahead|hours_ago):(\d{1,5})\}\}$")


class EraAiWatchlist(models.Model):
    """An audience the manager watches, and what to say to it.

    This is the seam that makes the module business-agnostic. A SaaS watches
    accounts that stopped issuing invoices; a clinic watches patients overdue
    for a check-up; a workshop watches cars due for service. All three are the
    same thing: a model, a domain, a reason, and a message. Encoding that as
    configuration rather than Python is what lets one module manage unrelated
    trades — and it lets the discovery step create audiences nobody thought to
    program.
    """

    _name = "era.ai.watchlist"
    _description = "Era AI Watchlist"
    _order = "priority, sequence, id"

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [("draft", "Proposed"), ("approved", "Approved")],
        default="draft",
        required=True,
        index=True,
        help="Only approved watchlists are ever acted on. Anything the AI "
             "creates lands here as Proposed and does nothing until a human "
             "approves it.",
    )
    blocked_reason = fields.Char(
        readonly=True,
        help="Why this could not be approved automatically.",
    )
    sequence = fields.Integer(default=10)
    priority = fields.Integer(
        default=50,
        required=True,
        help="Lower runs first. When one contact matches several watchlists "
             "only the lowest-priority one is acted on, so nobody receives two "
             "messages on the same morning.",
    )
    model_id = fields.Many2one(
        "ir.model",
        string="Model",
        required=True,
        ondelete="cascade",
        help="Which records this audience is made of.",
    )
    model_name = fields.Char(related="model_id.model", store=True, string="Model Name")
    domain = fields.Char(
        required=True,
        default="[]",
        help="Odoo domain selecting the records that need attention. For "
             "anything time-based use a relative token instead of a literal "
             "date, e.g. [('last_order_date', '<', '{{days_ago:60}}')] — a "
             "hardcoded date stops meaning what you meant a month later. "
             "Supported: {{days_ago:N}}, {{days_ahead:N}}, {{hours_ago:N}}.",
    )
    partner_field = fields.Char(
        default="partner_id",
        help="Field holding the contact to write to. Use 'id' when the records "
             "are themselves contacts. A to-many field works too — live chat, "
             "event registrations and similar link their customer that way — "
             "and the first contact with an email address is used.",
    )
    play = fields.Char(
        required=True,
        help="The kind of message this audience gets. Free text, so a business "
             "can name its own plays; the queue deduplicates on it.",
    )
    intent = fields.Text(
        required=True,
        help="What the message should achieve and which evidence to cite. This "
             "is handed to the AI verbatim, so write it the way you would brief "
             "a new employee.",
    )
    cooldown_days = fields.Integer(
        default=30,
        help="Do not repeat this play to the same contact within this many days.",
    )
    match_count = fields.Integer(compute="_compute_match_count")

    # ------------------------------------------------------------------
    # The approval gate, enforced rather than requested
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """Everything is born Proposed, whoever creates it.

        The agent needs create rights to propose audiences at all, and with
        them it could simply pass state='approved' and skip the review. So the
        state is not accepted from the caller: it is set here, and only the
        approval actions below may change it.
        """
        if not self.env.context.get("era_ai_approving"):
            vals_list = [dict(vals, state="draft") for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self.env.context.get("era_ai_approving"):
            vals = {key: value for key, value in vals.items() if key != "state"}
        return super().write(vals)

    @api.constrains("domain", "model_id", "partner_field")
    def _check_domain(self):
        """Fail here, loudly, rather than silently matching nothing at 3am."""
        for watchlist in self:
            model = self.env.get(watchlist.model_name)
            if model is None:
                raise ValidationError(
                    _("Model %s is not installed.", watchlist.model_name))
            try:
                domain = ast.literal_eval(watchlist.domain or "[]")
            except (ValueError, SyntaxError) as error:
                raise ValidationError(
                    _("The domain of '%(name)s' is not valid: %(err)s",
                      name=watchlist.name, err=error)) from error
            if not isinstance(domain, list):
                raise ValidationError(
                    _("The domain of '%s' must be a list.", watchlist.name))
            try:
                model.search_count(watchlist._resolve_domain(domain))
            except Exception as error:  # noqa: BLE001 - any ORM complaint means a bad domain
                raise ValidationError(
                    _("The domain of '%(name)s' does not run: %(err)s",
                      name=watchlist.name, err=error)) from error
            field = watchlist.partner_field
            if not field or field == "id":
                continue
            if field not in model._fields:
                raise ValidationError(
                    _("%(model)s has no field '%(field)s'.",
                      model=watchlist.model_name, field=field))
            definition = model._fields[field]
            if definition.comodel_name != "res.partner":
                raise ValidationError(
                    _("'%(field)s' on %(model)s does not point at a contact, "
                      "so there would be nobody to write to.",
                      field=field, model=watchlist.model_name))

    def _compute_match_count(self):
        for watchlist in self:
            watchlist.match_count = watchlist._matching_count()

    def _matching_count(self):
        self.ensure_one()
        try:
            return self.env[self.model_name].search_count(self._domain())
        except Exception:  # noqa: BLE001 - a broken watchlist must not break the list view
            return 0

    @staticmethod
    def _resolve_relative(value):
        """Turn a {{days_ago:60}} token into a datetime, or leave it alone."""
        if not isinstance(value, str):
            return value
        match = RELATIVE_DATE.match(value.strip())
        if not match:
            return value
        kind, amount = match.group(1), int(match.group(2))
        now = fields.Datetime.now()
        if kind == "days_ago":
            return fields.Datetime.to_string(now - timedelta(days=amount))
        if kind == "days_ahead":
            return fields.Datetime.to_string(now + timedelta(days=amount))
        return fields.Datetime.to_string(now - timedelta(hours=amount))

    def _resolve_domain(self, domain):
        resolved = []
        for leaf in domain:
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                field, operator, value = leaf
                resolved.append((field, operator, self._resolve_relative(value)))
            else:
                resolved.append(leaf)
        return resolved

    def _domain(self):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.domain or "[]")
        except (ValueError, SyntaxError):
            return [("id", "=", 0)]
        if not isinstance(domain, list):
            return [("id", "=", 0)]
        return self._resolve_domain(domain)

    def matching_records(self, limit=None):
        self.ensure_one()
        model = self.env.get(self.model_name)
        if model is None:
            return self.env["res.partner"].browse()
        return model.search(self._domain(), limit=limit)

    def partner_of(self, record):
        """The contact to write to for one matched record.

        A to-many link counts. Several models keep their customer that way —
        live chat's livechat_customer_partner_ids, event registrations,
        followers — and refusing them would mean those audiences can be
        watched but never contacted. The first contact with an email wins,
        because writing to one of them is the point.
        """
        self.ensure_one()
        if record._name == "res.partner" and self.partner_field in ("id", "", False):
            return record
        if self.partner_field not in record._fields:
            return self.env["res.partner"]
        value = record[self.partner_field]
        if not value or value._name != "res.partner":
            return self.env["res.partner"]
        if len(value) <= 1:
            return value
        with_email = value.filtered(lambda partner: partner.email)
        return (with_email or value)[0]

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------
    @api.model
    def _blast_limits(self):
        param = self.env["ir.config_parameter"].sudo()

        def _int(key, default):
            try:
                return int(param.get_param(key, default))
            except (TypeError, ValueError):
                return default

        return (_int("era_ai_manager.max_audience_absolute", 200),
                _int("era_ai_manager.max_audience_percent", 60))

    def _blast_radius_problem(self):
        """Refuse an 'audience' that is really the whole customer base.

        A domain of [] runs perfectly and matches everyone, so validity alone
        does not make a watchlist safe. This is the check that stands between a
        plausible-looking AI proposal and mailing every contact you have.
        """
        self.ensure_one()
        model = self.env.get(self.model_name)
        if model is None:
            return _("Model %s is not installed.", self.model_name)
        matches = self._matching_count()
        if not matches:
            return _("This matches nobody right now.")
        max_absolute, max_percent = self._blast_limits()
        if matches > max_absolute:
            return _("This matches %(matches)s records, over the limit of "
                     "%(limit)s. Narrow the domain, or approve it anyway if "
                     "you meant to reach that many.",
                     matches=matches, limit=max_absolute)
        total = model.search_count([])
        if total and (matches * 100 // total) >= max_percent:
            return _("This matches %(pct)s%% of all %(model)s records. That is "
                     "a mailing list, not an audience.",
                     pct=matches * 100 // total, model=self.model_name)
        return False

    def action_approve(self):
        """Approve only what is safe; say plainly why the rest is held."""
        for watchlist in self:
            problem = watchlist._blast_radius_problem()
            if problem:
                watchlist.with_context(era_ai_approving=True).write(
                    {"state": "draft", "blocked_reason": problem})
                continue
            watchlist.with_context(era_ai_approving=True).write(
                {"state": "approved", "blocked_reason": False})
        return True

    def action_approve_anyway(self):
        """The owner has read the warning and means it."""
        return self.with_context(era_ai_approving=True).write(
            {"state": "approved", "blocked_reason": False})

    def action_reset_to_draft(self):
        return self.with_context(era_ai_approving=True).write({"state": "draft"})

    def action_message_everyone(self):
        """Write one message to everyone this rule currently matches."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Message “%s”", self.name),
            "res_model": "era.ai.watchlist.compose",
            "view_mode": "form",
            "target": "new",
            "context": {"default_watchlist_id": self.id},
        }

    def action_open_matches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": self.model_name,
            "view_mode": "list,form",
            "domain": self._domain(),
        }

    @api.model
    def due_records(self, limit_per_list=15):
        """Everything needing attention, best play first, one per contact.

        Only approved watchlists count. De-duplication across them happens
        here rather than in the agent's head: the lowest priority number wins,
        so a customer who is both dormant and low on credit hears about one of
        them, not both.
        """
        seen = set()
        result = []
        # The filter that makes "let it discover for itself" safe: whatever the
        # agent creates is inert until a human approves it.
        for watchlist in self.search([("state", "=", "approved")]):
            for record in watchlist.matching_records(limit=limit_per_list):
                partner = watchlist.partner_of(record)
                key = ("partner", partner.id) if partner else ("rec", record._name, record.id)
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "watchlist": watchlist,
                    "record": record,
                    "partner": partner,
                })
        return result

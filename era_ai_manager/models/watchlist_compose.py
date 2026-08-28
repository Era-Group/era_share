import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

COPYWRITER_PROMPT = """You write one short message that will be sent to a
whole audience at once, on behalf of the business described to you.

You are given: what the business is, the audience's name, the intent behind
writing to them (what the message must achieve and which evidence to cite),
and a few real examples of the people who will receive it.

Rules:
- Write in the language you are told to write in, and only that language.
- Three to six sentences. No corporate padding, no exclamation marks.
- Exactly one clear next step.
- It goes to everyone on the list, so never claim a specific fact about an
  individual that you were not given for all of them. Where a name belongs,
  write the placeholder {name} and nothing else.
- Never invent a product, a price, a discount, a date or a deadline.
- Never shame or pressure a quiet customer. Offer help, not guilt.
- Simple HTML only: <p>, <br/>, <ul>, <li>, <strong>, <a href>.

Reply with STRICT JSON and nothing else:
{"subject": "...", "body_html": "<p>...</p>"}"""


def extract_json(raw):
    """Pull a JSON object out of an LLM reply.

    Models wrap JSON in prose or fences however they feel that day. Returns
    None rather than raising, so a bad reply is reported to the user instead
    of becoming a traceback.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None


class EraAiWatchlistCompose(models.TransientModel):
    """Write one message to everyone a watchlist currently matches.

    The agents personalise each message; sometimes the owner just wants to say
    the same thing to all of them, now. This is that — and it deliberately
    goes through the same outreach queue rather than around it, so the
    frequency cap, the opt-out, the deduplication and the send window still
    apply to every draft it creates. A bulk action is exactly when those
    matter most.
    """

    _name = "era.ai.watchlist.compose"
    _description = "Message a Watchlist"

    watchlist_id = fields.Many2one(
        "era.ai.watchlist", required=True, readonly=True, ondelete="cascade"
    )
    # Not required: the AI fills these when the wizard opens, and a provider
    # that is down must not leave the owner staring at a form they cannot
    # close. Emptiness is refused at the point of creating drafts instead.
    subject = fields.Char()
    body_html = fields.Html(
        sanitize=True,
        help="Write it once. Use {name} where the contact's name should go.",
    )
    ai_note = fields.Char(readonly=True)
    lang = fields.Selection(
        [("ar_001", "Arabic"), ("en_US", "English")],
        required=True,
        default=lambda self: self.env["era.ai.profile"].owner_language(),
    )
    matched_count = fields.Integer(
        string="Matches the rule", compute="_compute_counts"
    )
    reachable_count = fields.Integer(
        string="Will be drafted", compute="_compute_counts"
    )
    skipped_note = fields.Text(compute="_compute_counts")
    ai_available = fields.Boolean(compute="_compute_ai_available")
    will_send_now = fields.Boolean(compute="_compute_autonomy")
    autonomy_note = fields.Char(compute="_compute_autonomy")

    def _compute_autonomy(self):
        """Say which of the two things the button does, before it is pressed.

        In Full autonomy the owner has already said they do not want to
        approve each message, so queuing these for approval would ignore that
        instruction. The button therefore sends — and says so first.
        """
        full = self.env["era.ai.outreach"]._autonomy_mode() == "full"
        for wizard in self:
            wizard.will_send_now = full
            wizard.autonomy_note = _(
                "You are in Full autonomy, so pressing this sends the messages "
                "— only the guardrails stand between them and the customer."
            ) if full else _(
                "You are in Ramp mode, so these wait in the queue for your "
                "approval. Nothing is sent yet."
            )

    def _compute_ai_available(self):
        # A soft dependency on purpose: this module installs on Community,
        # where Odoo's AI is simply absent. The button hides rather than the
        # module refusing to install.
        available = self.env.get("ai.agent") is not None
        for wizard in self:
            wizard.ai_available = available

    @api.model
    def default_get(self, fields_list):
        """Open with the message already written.

        The watchlist's intent already says what this message has to achieve,
        so asking the owner to describe it again is asking a question the
        system can answer. It proposes; the owner edits or rewrites.
        """
        values = super().default_get(fields_list)
        watchlist_id = values.get("watchlist_id") or self.env.context.get(
            "default_watchlist_id")
        if not watchlist_id or values.get("body_html"):
            return values
        wizard = self.new(dict(values, watchlist_id=watchlist_id))
        try:
            drafted = wizard._draft_with_ai()
        except Exception as error:  # noqa: BLE001 - opening must never fail
            _logger.info("Could not pre-write the message: %s", error)
            values["ai_note"] = _(
                "I could not write a first draft (%s). Write it yourself, or "
                "try the rewrite button.", error)
            return values
        values.update(drafted)
        values["ai_note"] = _(
            "I wrote this from the audience's intent. Edit it, rewrite it, or "
            "send it as it is.")
        return values

    @api.depends("watchlist_id")
    def _compute_counts(self):
        for wizard in self:
            plan = wizard._plan()
            wizard.matched_count = plan["matched"]
            wizard.reachable_count = len(plan["targets"])
            notes = []
            if plan["no_contact"]:
                notes.append(_(
                    "%s are not linked to a contact at all — an anonymous "
                    "visitor, for instance — so there is nobody to write to. "
                    "Watching them is still useful; reaching them by email is "
                    "not possible.", plan["no_contact"]))
            if plan["no_email"]:
                notes.append(_("%s have no email address and will be skipped.",
                               plan["no_email"]))
            if plan["already_queued"]:
                notes.append(_("%s already have a message waiting and will be "
                               "skipped, so nobody is written to twice.",
                               plan["already_queued"]))
            wizard.skipped_note = "\n".join(notes)

    def _plan(self):
        """Who would actually be written to, and who would not, and why.

        Computed before anything is created so the owner sees the real number
        rather than the number the rule matches — those are rarely the same,
        and the difference is the whole risk of a bulk send.
        """
        self.ensure_one()
        watchlist = self.watchlist_id
        if not watchlist:
            return {"matched": 0, "targets": [], "no_email": 0,
                    "already_queued": 0, "no_contact": 0}
        Outreach = self.env["era.ai.outreach"].sudo()
        records = watchlist.matching_records()
        seen_partners = set()
        targets, no_email, already, no_contact = [], 0, 0, 0
        for record in records:
            partner = watchlist.partner_of(record)
            if not partner:
                # Nobody behind the record. Saying so beats a silent zero:
                # "5 match, 0 will be written to" with no reason reads like a
                # bug, when it is really the shape of the data.
                no_contact += 1
                continue
            if partner.id in seen_partners:
                continue
            seen_partners.add(partner.id)
            if not partner.email:
                no_email += 1
                continue
            if Outreach.search_count([
                ("partner_id", "=", partner.id),
                ("state", "in", ("draft", "pending", "approved")),
            ]):
                already += 1
                continue
            targets.append((record, partner))
        return {"matched": len(records), "targets": targets,
                "no_email": no_email, "already_queued": already,
                "no_contact": no_contact}

    # ------------------------------------------------------------------
    # Letting the AI write it
    # ------------------------------------------------------------------
    def _copywriter(self):
        """A synchronous writer.

        aidoo is asynchronous by design — a daemon thread and a CLI
        subprocess — so it cannot fill a field on a form the user is looking
        at. Odoo's own ai.agent answers in the request, which is what a button
        needs. The agent is created once and then reused.
        """
        Agent = self.env.get("ai.agent")
        if Agent is None:
            raise UserError(_(
                "Writing with AI needs Odoo's AI app, which is not installed "
                "here. You can still write the message yourself."))
        param = self.env["ir.config_parameter"].sudo()
        stored = param.get_param("era_ai_manager.copywriter_agent_id")
        if stored:
            agent = Agent.sudo().browse(int(stored)).exists()
            if agent:
                return agent
        values = {
            "name": _("AI Manager — Copywriter"),
            "subtitle": _("Writes one message for a whole audience."),
            "system_prompt": COPYWRITER_PROMPT,
            "response_style": "balanced",
        }
        # Adopt whatever routing this database already uses successfully,
        # rather than the field defaults. The default llm_model is gpt-4o,
        # which needs an OpenAI key most deployments do not have; and where a
        # provider add-on routes agents through its own accounts, an agent
        # without that link falls back to core and fails for a missing key
        # while every other agent on the system works.
        working = Agent.sudo().search(
            [("active", "=", True)], order="id", limit=1)
        if working:
            for field in ("llm_model", "era_account_id", "era_model_id"):
                if field in Agent._fields and working[field]:
                    value = working[field]
                    values[field] = value.id if hasattr(value, "id") else value
        agent = Agent.sudo().create(values)
        param.set_param("era_ai_manager.copywriter_agent_id", str(agent.id))
        return agent

    def _audience_examples(self, limit=5):
        """A few real recipients, so the copy is about someone.

        Only the display name and the fields the watchlist itself reasons
        about — enough to ground the writing without handing the model the
        customer base.
        """
        self.ensure_one()
        examples = []
        for record, partner in self._plan()["targets"][:limit]:
            entry = {"name": partner.display_name}
            for field in ("email",):
                if field in record._fields:
                    entry[field] = bool(record[field])
            examples.append(entry)
        return examples

    def _draft_with_ai(self):
        """Ask the copywriter for {subject, body_html}. Raises on failure."""
        self.ensure_one()
        watchlist = self.watchlist_id
        profile = self.env["era.ai.profile"].sudo().current()
        payload = {
            "business": (profile.business_summary or "")[:2000],
            "audience_name": watchlist.name,
            "intent": watchlist.intent,
            "play": watchlist.play,
            "recipients_now": self.reachable_count,
            "examples": self._audience_examples(),
            "write_in_language": self.lang,
        }
        agent = self._copywriter()
        try:
            response = agent.sudo().get_direct_response(
                prompt=json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as error:  # noqa: BLE001 - a provider problem is the user's to see
            raise UserError(_(
                "The AI could not be reached: %s\n\nCheck the AI provider "
                "configuration, or write the message yourself.", error)) from error
        data = extract_json(response[0] if response else "")
        if not isinstance(data, dict) or not data.get("body_html"):
            raise UserError(_(
                "The AI did not return a usable message. Try again, or write "
                "it yourself."))
        return {"subject": data.get("subject") or self.subject,
                "body_html": data["body_html"]}

    def action_write_with_ai(self):
        """Rewrite it — an option, not the way in."""
        self.ensure_one()
        self.write(self._draft_with_ai())
        # Reopen the same wizard so the user reads it before anything is drafted.
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _render(self, partner):
        """The one substitution worth having without a templating engine."""
        self.ensure_one()
        name = partner.display_name or ""
        return (self.body_html or "").replace("{name}", name)

    def action_create_drafts(self):
        self.ensure_one()
        if not (self.body_html or "").strip():
            raise UserError(_(
                "There is no message to send. Write one, or press the rewrite "
                "button to have it written."))
        plan = self._plan()
        if not plan["targets"]:
            if plan["no_contact"] and not (plan["no_email"]
                                           or plan["already_queued"]):
                raise UserError(_(
                    "There is nobody to write to. The rule matches %(matched)s "
                    "record(s), but none of them is linked to a contact — they "
                    "are anonymous. This audience can be watched and reported "
                    "on, but not emailed.", matched=plan["matched"]))
            raise UserError(_(
                "There is nobody to write to. The rule matches %(matched)s "
                "record(s): %(no_contact)s have no contact behind them, "
                "%(no_email)s have no email address, and %(queued)s already "
                "have a message waiting.",
                matched=plan["matched"], no_contact=plan["no_contact"],
                no_email=plan["no_email"], queued=plan["already_queued"]))

        Outreach = self.env["era.ai.outreach"].sudo()
        created = Outreach.browse()
        for record, partner in plan["targets"]:
            created |= Outreach.create({
                "subject": self.subject,
                "body_html": self._render(partner),
                "lang": self.lang,
                "channel": "email",
                "play": self.watchlist_id.play,
                "agent_name": "manual",
                "partner_id": partner.id,
                "watchlist_id": self.watchlist_id.id,
                "email_to": partner.email,
                "rationale": _(
                    "Written by hand to everyone on “%s”.",
                    self.watchlist_id.name),
            })

        # Straight into the normal flow: in Ramp they wait for approval, in
        # Full autonomy the guardrails decide. Bypassing that here would make
        # this button the one hole in the wall.
        created.action_submit()
        if self.env["era.ai.outreach"]._autonomy_mode() == "full":
            # Deliver now rather than at the next cron tick: the owner pressed
            # a button and is waiting to see what happened.
            created.filtered(lambda draft: draft.state == "approved")._deliver()
        blocked = created.filtered(lambda draft: draft.state == "blocked")
        sent = created.filtered(lambda draft: draft.state == "sent")

        action = self.env.ref(
            "era_ai_manager.action_era_ai_outreach", raise_if_not_found=False)
        result = {
            "type": "ir.actions.act_window",
            "name": _("Messages to %s", self.watchlist_id.name),
            "res_model": "era.ai.outreach",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
            "target": "current",
        }
        if action:
            result["search_view_id"] = action.search_view_id.id
        if sent:
            result["name"] = _("Sent to %(sent)s of %(total)s",
                               sent=len(sent), total=len(created))
        if blocked:
            result["name"] = _(
                "%(name)s — %(blocked)s blocked",
                name=result["name"], blocked=len(blocked))
        return result

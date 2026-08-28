import json
import logging
import re
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Deliberately loose and country-neutral: this module runs for businesses whose
# numbers we cannot predict. Digits are counted afterwards.
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{6,}\d")

# Addresses that are examples rather than customers. Chat assistants hand these
# out while explaining what they need ("send me your email, like name@example.com"),
# and harvesting one would create a contact for a person who does not exist.
PLACEHOLDER_DOMAINS = frozenset((
    "example.com", "example.org", "example.net", "domain.com", "yourdomain.com",
    "mydomain.com", "sample.com", "test.com", "email.com", "company.com",
))

# What a broken assistant says. These stay English on purpose: a provider error
# surfaces in English no matter what language the shop trades in, which is
# exactly what makes the check portable — and what makes it so glaring to a
# customer reading an otherwise Arabic conversation.
BREAKAGE_MARKERS = (
    "api key", "api_key", "access token", "traceback (most recent call last)",
    "cli error", "rate limit", "quota exceeded", "insufficient_quota",
    "internal server error", "connection refused", "connection error",
    "service unavailable", "bad gateway", "timed out", "timeout error",
    "processing loop ended", "no response from", "unhandled exception",
    "invalid_request_error", "authentication failed", "401 unauthorized",
    "403 forbidden", "500 server error", "undefined is not", "nonetype",
    # Added after a live miss: the assistant replied "The Codex CLI returned
    # an empty response" and the check scored the day as healthy. Any list of
    # literals is incomplete by construction, which is why the parameter
    # below exists — a new provider invents new wording, and waiting for a
    # release to notice it means a week of unanswered customers.
    "empty response", "cli returned", "codex", "claude cli", "no response",
    "returned nothing", "model overloaded", "context length",
)


def breakage_markers(env):
    """The built-in list plus whatever this deployment has learnt to add.

    era_ai_manager.breakage_markers holds extra comma-separated phrases.
    """
    extra = env["ir.config_parameter"].sudo().get_param(
        "era_ai_manager.breakage_markers", "")
    learnt = tuple(
        phrase.strip().lower() for phrase in extra.split(",") if phrase.strip())
    return BREAKAGE_MARKERS + learnt

REPLY_PROMPT = """You answer a customer whose live chat ended without them \
being helped. Reply with JSON only, no prose, no code fence:

{"subject": "", "body_html": "", "answered": true}

- Write in the same language the customer used in the conversation.
- If the transcript makes their question clear enough to answer, answer it \
as fully as the business facts allow, then invite them to reply if anything \
is still unclear. Set "answered" to true.
- If it is not clear what they needed, do not guess and do not pad. Say the \
chat ended before they got an answer, restate what you understood they were \
asking, and ask them to reply with the missing detail. Set "answered" to false.
- Never invent prices, dates, policies or capabilities. If a fact is not in \
the business summary or the transcript, ask instead of asserting.
- Greet them by name if we know it. Keep it short: a customer who was already \
failed once will not read six paragraphs.
- body_html is simple HTML: <p> paragraphs, nothing else."""

EXTRACT_PROMPT = """You read one customer conversation and report what it \
tells us about the person. Reply with JSON only, no prose, no code fence:

{"name": "", "interest": "", "summary": "", "kind": "sales|support|noise", \
"urgency": "high|normal|low"}

- name: the customer's own name if they gave it, else "".
- interest: at most 8 words, what they actually wanted.
- summary: 2-3 sentences for a busy manager — who this is, what they asked, \
where it was left, what to do next. Write it in the language given as \
"reply_language".
- kind: "sales" if they asked about price, features, buying or signing up; \
"support" if something is not working for an existing customer; "noise" if \
there is nothing worth following up.
- urgency: "high" if they were leaving, angry, blocked or cancelling.

Never invent contact details. Report only what the customer themselves said."""


class EraAiConversation(models.Model):
    """A chat that ended, read and turned into something actionable.

    Live chat is the one channel where a stranger says exactly what they want
    and then disappears without a trace. The transcript is already in the
    database; nobody reads it. This harvests each finished conversation once:
    it pulls out the contact details the visitor typed, notices whether the
    assistant actually helped, and files a lead or a ticket so a real follow-up
    exists instead of a chat log.

    Reads the visitor's own words only. An address the assistant typed while
    asking for one is an example, not a customer.
    """

    _name = "era.ai.conversation"
    _description = "Era AI Harvested Conversation"
    _order = "ended_at desc, id desc"
    _rec_name = "title"

    title = fields.Char(required=True, index=True)
    # Generic on purpose, exactly like the outreach queue: live chat today,
    # any other threaded conversation later, and installable on a database
    # that has neither.
    thread_model = fields.Char(string="Source Model", required=True, index=True)
    thread_id = fields.Integer(string="Source Record", required=True, index=True)

    started_at = fields.Datetime(readonly=True, index=True)
    ended_at = fields.Datetime(readonly=True, index=True)
    message_count = fields.Integer(readonly=True)
    transcript = fields.Text(readonly=True, help="What was actually said.")

    visitor_name = fields.Char(index=True)
    email = fields.Char(index=True)
    phone = fields.Char()
    partner_id = fields.Many2one("res.partner", ondelete="set null", index=True)

    interest = fields.Char(help="What this person wanted, in a few words.")
    summary = fields.Text(help="What happened and what to do about it.")
    kind = fields.Selection(
        [("sales", "Sales enquiry"), ("support", "Support issue"),
         ("noise", "Nothing to follow up")],
        default="sales", required=True, index=True,
    )
    urgency = fields.Selection(
        [("high", "High"), ("normal", "Normal"), ("low", "Low")],
        default="normal", required=True,
    )

    has_contact = fields.Boolean(
        compute="_compute_has_contact", store=True,
        help="The visitor left an email address or a phone number.")
    assistant_failed = fields.Boolean(
        readonly=True, index=True,
        help="The assistant replied with a technical error the customer could "
             "read. Every one of these is a visitor who saw the system break.")
    left_unanswered = fields.Boolean(
        readonly=True,
        help="The last word was the customer's and nobody answered.")
    poor_rating = fields.Boolean(readonly=True)

    state = fields.Selection(
        [("new", "To review"), ("converted", "Followed up"),
         ("ignored", "Ignored")],
        default="new", required=True, index=True,
    )
    result_model = fields.Char(string="Follow-up Model", readonly=True)
    result_id = fields.Integer(string="Follow-up Record", readonly=True)
    result_ref = fields.Char(compute="_compute_result_ref", string="Follow-up")

    # Harvesting twice would mean two leads for one customer. Odoo 19 dropped
    # _sql_constraints; declared the old way this silently creates no
    # constraint at all, which is worse than no idempotency claim.
    _source_unique = models.Constraint(
        "UNIQUE (thread_model, thread_id)",
        "This conversation has already been harvested.",
    )

    @api.depends("email", "phone", "partner_id", "partner_id.email")
    def _compute_has_contact(self):
        for record in self:
            record.has_contact = bool(
                record.email or record.phone or record.partner_id)

    def _reply_address(self):
        """The address to answer on, from the chat or from the contact."""
        self.ensure_one()
        return self.email or self.partner_id.email or False

    @api.depends("result_model", "result_id")
    def _compute_result_ref(self):
        for record in self:
            target = record._result_record()
            record.result_ref = target.display_name if target else False

    def _result_record(self):
        self.ensure_one()
        if not self.result_model or not self.result_id:
            return False
        model = self.env.get(self.result_model)
        if model is None:
            return False
        record = model.sudo().browse(self.result_id).exists()
        return record or False

    # ------------------------------------------------------------------
    # Reading a conversation
    # ------------------------------------------------------------------
    @api.model
    def _is_visitor_message(self, message, operator):
        """Did the customer write this, or did our side?

        The distinction decides everything downstream. An email address in an
        operator's message is an example being requested; the same address in
        the visitor's message is a lead.
        """
        if message.author_guest_id:
            return True
        author = message.author_id
        if not author or author == operator:
            return False
        if author.user_ids.filtered(lambda user: not user.share):
            return False  # a member of staff
        return True

    @api.model
    def _visitor_partners(self, channel):
        """Whoever live chat itself considers the customer side."""
        field = "livechat_customer_partner_ids"
        if field in channel._fields:
            return channel[field]
        return self.env["res.partner"]

    @api.model
    def _our_own_addresses(self):
        """Addresses that belong to us, and so can never be a lead.

        An assistant quoting the support address, a staff member signing off —
        harvesting either would create a contact for our own company and then
        email ourselves about it. Cheap to check, embarrassing to miss.
        """
        addresses = set()
        users = self.env["res.users"].sudo().search(
            [("share", "=", False), ("email", "!=", False)])
        addresses |= {(email or "").lower() for email in users.mapped("email")}
        companies = self.env["res.company"].sudo().search([])
        addresses |= {(email or "").lower() for email in companies.mapped("email")}
        param = self.env["ir.config_parameter"].sudo()
        for key in ("era_ai_manager.owner_email", "era_ai_manager.mail_from"):
            value = param.get_param(key)
            if value:
                addresses.add(value.lower())
        alias = self.env.get("mail.alias.domain")
        if alias is not None:
            for domain in alias.sudo().search([]):
                for field in ("catchall_email", "bounce_email", "default_from_email"):
                    if field in domain._fields and domain[field]:
                        addresses.add(domain[field].lower())
        return {address for address in addresses if address}

    @api.model
    def _read_conversation(self, channel):
        """Everything one channel tells us, before anything is created."""
        operator = (channel.livechat_operator_id
                    if "livechat_operator_id" in channel._fields
                    else self.env["res.partner"])
        customers = self._visitor_partners(channel)
        lines, visitor_text, our_text = [], [], []
        visitor_label = ""
        wrote_in = self.env["res.partner"]
        messages = channel.message_ids.sorted("id")
        spoken = 0
        for message in messages:
            if message.message_type == "notification":
                continue
            body = html2plaintext(message.body or "").strip()
            if not body:
                continue
            spoken += 1
            from_visitor = (message.author_id in customers
                            if message.author_id and customers
                            else self._is_visitor_message(message, operator))
            who = (message.author_guest_id.name or message.author_id.name
                   or _("Visitor")) if from_visitor else (
                       message.author_id.name or _("Assistant"))
            if from_visitor and not visitor_label:
                visitor_label = who
            # Only a partner who is the author in their own right. A guest
            # message still carries a default author_id — whoever's session
            # created it — and treating that as the customer would file an
            # anonymous chat under a member of staff.
            if (from_visitor and message.author_id
                    and not message.author_guest_id
                    and not message.author_id.user_ids.filtered(
                        lambda user: not user.share)):
                wrote_in |= message.author_id
            lines.append("%s: %s" % (who, body))
            (visitor_text if from_visitor else our_text).append(body)

        ours = self._our_own_addresses()
        emails, phones = [], []
        for text in visitor_text:
            emails += [
                address for address in EMAIL_RE.findall(text)
                if address.split("@")[-1].lower() not in PLACEHOLDER_DOMAINS
                and address.lower() not in ours
            ]
            for raw in PHONE_RE.findall(text):
                digits = re.sub(r"\D", "", raw)
                if 8 <= len(digits) <= 15:
                    phones.append(raw.strip())

        haystack = " ".join(our_text).lower()
        failed = any(marker in haystack
                     for marker in breakage_markers(self.env))

        last_from_visitor = False
        for message in reversed(messages):
            if message.message_type == "notification" or not (
                    html2plaintext(message.body or "").strip()):
                continue
            last_from_visitor = (
                message.author_id in customers
                if message.author_id and customers
                else self._is_visitor_message(message, operator))
            break

        rating = 0
        if "rating_last_value" in channel._fields:
            rating = channel.rating_last_value or 0

        # livechat_customer_partner_ids is computed from live chat's own
        # member history, so it is empty whenever the session was not opened
        # through the widget. A partner who plainly wrote in the chat and is
        # not staff is the customer, whatever the history says.
        identified = (customers or wrote_in)[:1]
        return {
            "partner": identified,
            # Whoever the visitor appeared as. Live chat names the channel
            # after the operator, so falling back to the channel title labels
            # every anonymous visitor with the assistant's own name.
            "visitor_label": visitor_label,
            "transcript": "\n".join(lines)[:12000],
            "message_count": spoken,
            "email": emails[0] if emails else False,
            "phone": phones[0] if phones else False,
            "visitor_text": " ".join(visitor_text)[:4000],
            "assistant_failed": failed,
            "left_unanswered": last_from_visitor,
            "poor_rating": bool(rating and rating <= 2),
        }

    @api.model
    def count_broken_conversations(self, days=7):
        """Chats where our own side answered with a technical error.

        Read from the channels themselves, not from the harvested records.
        The conversations that expose a broken assistant are overwhelmingly
        the anonymous ones — an error message is what stops a visitor before
        they ever leave an address — and those are deliberately not filed.
        Counting the records instead of the channels would have made the
        health check go quiet exactly when the assistant broke hardest.
        """
        Channel = self.env.get("discuss.channel")
        if Channel is None or "channel_type" not in Channel._fields:
            return 0
        since = fields.Datetime.now() - timedelta(days=days)
        try:
            channels = Channel.sudo().search([
                ("channel_type", "=", "livechat"),
                ("create_date", ">=", since),
            ])
        except Exception:  # noqa: BLE001 - live chat absent or shaped otherwise
            return 0
        if not channels:
            return 0
        markers = breakage_markers(self.env)
        marker_domain = ["|"] * (len(markers) - 1) + [
            ("body", "ilike", marker) for marker in markers]
        messages = self.env["mail.message"].sudo().search([
            ("model", "=", "discuss.channel"),
            ("res_id", "in", channels.ids),
            ("author_guest_id", "=", False),  # our side, not the visitor's
        ] + marker_domain)
        return len(set(messages.mapped("res_id")))

    # ------------------------------------------------------------------
    # Harvesting
    # ------------------------------------------------------------------
    @api.model
    def _harvestable_channels(self, limit=200):
        """Finished live chats nobody has read yet."""
        Channel = self.env.get("discuss.channel")
        if Channel is None or "channel_type" not in Channel._fields:
            return []
        param = self.env["ir.config_parameter"].sudo()
        try:
            days = int(param.get_param(
                "era_ai_manager.conversation_lookback_days", "30"))
        except (TypeError, ValueError):
            days = 30
        domain = [("channel_type", "=", "livechat")]
        if "livechat_end_dt" in Channel._fields:
            domain.append(("livechat_end_dt", "!=", False))
        since = fields.Datetime.now() - timedelta(days=days)
        domain.append(("create_date", ">=", since))
        try:
            channels = Channel.sudo().search(domain, order="id", limit=limit)
        except Exception:  # noqa: BLE001 - livechat absent or shaped otherwise
            return []
        done = set(self.sudo().search([
            ("thread_model", "=", "discuss.channel")]).mapped("thread_id"))
        return [channel for channel in channels if channel.id not in done]

    @api.model
    def _harvest_one(self, channel):
        """One conversation in, one record out. Never raises for one bad row."""
        reading = self._read_conversation(channel)
        if not reading["message_count"]:
            return self.browse()
        # An anonymous visitor with no address and no number cannot be
        # answered, cannot be filed and cannot be followed up. Putting them on
        # a review list is asking someone to look at a row and then close it
        # again. The conversation still counts towards the health checks,
        # which read the channels directly rather than these records.
        if not (reading["email"] or reading["phone"] or reading["partner"]):
            return self.browse()
        record = self.sudo().create({
            "title": reading["visitor_label"] or channel.display_name
                     or _("Conversation"),
            "visitor_name": reading["visitor_label"] or False,
            "thread_model": "discuss.channel",
            "thread_id": channel.id,
            "started_at": channel.create_date,
            "ended_at": (channel.livechat_end_dt
                         if "livechat_end_dt" in channel._fields else False)
                        or channel.write_date,
            "message_count": reading["message_count"],
            "transcript": reading["transcript"],
            "email": reading["email"],
            "phone": reading["phone"],
            "assistant_failed": reading["assistant_failed"],
            "left_unanswered": reading["left_unanswered"],
            "poor_rating": reading["poor_rating"],
            "partner_id": reading["partner"].id if reading["partner"] else False,
            "kind": "sales",
        })
        record._describe(reading["visitor_text"])
        return record

    def _describe(self, visitor_text):
        """Have the AI say who this is. Silence is acceptable; failure is not.

        Without AI the record still carries the transcript, the email and the
        signals — everything needed to act. The summary is an improvement on
        that, not a precondition for it.
        """
        self.ensure_one()
        Agent = self.env.get("ai.agent")
        if Agent is None:
            return self._describe_without_ai()
        lang = self.env["era.ai.profile"].owner_language()
        payload = {
            "reply_language": lang,
            "transcript": self.transcript or "",
            "customer_said": visitor_text,
        }
        try:
            agent = self._reader_agent()
            response = agent.sudo().get_direct_response(
                prompt=json.dumps(payload, ensure_ascii=False, default=str))
            data = self._as_json(response[0] if response else "")
        except Exception as error:  # noqa: BLE001 - never lose a harvest to a provider
            _logger.info("Could not read conversation %s: %s", self.id, error)
            return self._describe_without_ai()
        if not isinstance(data, dict):
            return self._describe_without_ai()
        values = {}
        if data.get("name"):
            values["visitor_name"] = str(data["name"])[:80]
        if data.get("interest"):
            values["interest"] = str(data["interest"])[:120]
        if data.get("summary"):
            values["summary"] = str(data["summary"])[:2000]
        if data.get("kind") in ("sales", "support", "noise"):
            values["kind"] = data["kind"]
        if data.get("urgency") in ("high", "normal", "low"):
            values["urgency"] = data["urgency"]
        self.sudo().write(values)
        if not self.visitor_name:
            self._describe_without_ai()
        return True

    def _describe_without_ai(self):
        """A usable record with no model involved."""
        self.ensure_one()
        values = {}
        if not self.visitor_name:
            local = (self.email or "").split("@")[0]
            values["visitor_name"] = (local.replace(".", " ").title()
                                      or _("Unidentified visitor"))[:80]
        if not self.summary:
            values["summary"] = (self.transcript or "")[:600]
        self.sudo().write(values)
        return True

    @api.model
    def _as_json(self, text):
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except ValueError:
            return None

    @api.model
    def _reader_agent(self):
        Agent = self.env["ai.agent"]
        param = self.env["ir.config_parameter"].sudo()
        stored = param.get_param("era_ai_manager.reader_agent_id")
        if stored:
            agent = Agent.sudo().browse(int(stored)).exists()
            if agent:
                return agent
        values = {
            "name": _("AI Manager — Conversation reader"),
            "subtitle": _("Reads finished chats and says who was on the other side."),
            "system_prompt": EXTRACT_PROMPT,
            "response_style": "balanced",
        }
        # Same reasoning as the copywriter: adopt the routing this database is
        # already using, rather than the field defaults, which point at a
        # provider most deployments have no key for.
        working = Agent.sudo().search([("active", "=", True)], order="id", limit=1)
        if working:
            for field in ("llm_model", "era_account_id", "era_model_id"):
                if field in Agent._fields and working[field]:
                    value = working[field]
                    values[field] = value.id if hasattr(value, "id") else value
        agent = Agent.sudo().create(values)
        param.set_param("era_ai_manager.reader_agent_id", str(agent.id))
        return agent

    # ------------------------------------------------------------------
    # Turning it into something someone will act on
    # ------------------------------------------------------------------
    def _ensure_partner(self):
        """Match the contact, or create one — but only for a real address."""
        self.ensure_one()
        if self.partner_id:
            return self.partner_id
        Partner = self.env["res.partner"].sudo()
        partner = Partner.browse()
        if self.email:
            partner = Partner.search([("email", "=ilike", self.email)], limit=1)
        if not partner and self.phone:
            partner = Partner.search([("phone", "=", self.phone)], limit=1)
        if not partner:
            if not self.email and not self.phone:
                return Partner.browse()  # a name alone is not a contact
            partner = Partner.create({
                "name": self.visitor_name or self.email or self.phone,
                "email": self.email or False,
                "phone": self.phone or False,
                "comment": _("Created from a live chat conversation."),
            })
        self.sudo().write({"partner_id": partner.id})
        return partner

    def _follow_up_model(self):
        """Where this belongs, given what is installed here."""
        self.ensure_one()
        preferred = ("helpdesk.ticket", "crm.lead") if self.kind == "support" \
            else ("crm.lead", "helpdesk.ticket")
        for name in preferred:
            if self.env.get(name) is not None:
                return name
        return None

    def _body_field(self, model):
        for name in ("description", "comment", "note"):
            if name in model._fields:
                return name
        return None

    def action_convert(self):
        """Create the lead or ticket. Idempotent by design."""
        for record in self:
            if record.state == "converted" and record._result_record():
                continue
            if record.kind == "noise":
                raise UserError(_(
                    "This conversation was marked as nothing to follow up. "
                    "Change its kind first if you disagree."))
            partner = record._ensure_partner()
            if not partner:
                raise UserError(_(
                    "This visitor left no email address and no phone number, "
                    "so there is no one to follow up with. The conversation is "
                    "still worth reading, but it cannot become a lead."))
            model_name = record._follow_up_model()
            if not model_name:
                raise UserError(_(
                    "Neither CRM nor Helpdesk is installed, so there is "
                    "nowhere to file this. Install one of them first."))
            model = self.env[model_name].sudo()
            values = {
                "name": record.interest or record.title or _("Live chat"),
                "partner_id": partner.id,
            }
            body_field = record._body_field(model)
            if body_field:
                text = "%s\n\n%s\n\n%s" % (
                    record.summary or "", _("--- Conversation ---"),
                    record.transcript or "")
                values[body_field] = (
                    Markup("<pre>%s</pre>") % text
                    if model._fields[body_field].type == "html" else text)
            for field, value in (("email_from", record.email),
                                 ("phone", record.phone),
                                 ("contact_name", record.visitor_name)):
                if value and field in model._fields:
                    values[field] = value
            if "priority" in model._fields and record.urgency == "high":
                # Priority selections differ per model; take the highest the
                # model actually offers rather than assuming "3".
                choices = model._fields["priority"].get_values(self.env)
                if choices:
                    values["priority"] = choices[-1]
            try:
                target = model.create(values)
            except Exception as error:  # noqa: BLE001 - report, do not crash a cron
                raise UserError(_(
                    "Could not create the %(model)s: %(error)s",
                    model=model_name, error=error)) from error
            record.sudo().write({
                "state": "converted",
                "result_model": model_name,
                "result_id": target.id,
            })
            # Filing it is not answering it. The customer's own question is
            # still unanswered until something reaches their inbox.
            if record._reply_address():
                record._queue_reply()
        return True

    # ------------------------------------------------------------------
    # Answering the customer
    # ------------------------------------------------------------------
    def _draft_reply(self):
        """What to say to them. Falls back to an honest short note."""
        self.ensure_one()
        profile = self.env["era.ai.profile"].sudo().current()
        fallback_subject = _("About your chat with us")
        fallback_body = "<p>%s</p><p>%s</p>" % (
            _("Hello %s,", self.visitor_name or "") if self.visitor_name
            else _("Hello,"),
            _("You wrote to us on our website and the chat ended before you "
              "had a proper answer. Reply to this email with what you needed "
              "and a person will take it from here."),
        )
        Agent = self.env.get("ai.agent")
        if Agent is None:
            return fallback_subject, fallback_body
        payload = {
            "business": (profile.business_summary or "")[:2000],
            "customer_name": self.visitor_name or "",
            "what_they_wanted": self.interest or "",
            "transcript": self.transcript or "",
        }
        try:
            agent = self._reply_agent()
            response = agent.sudo().get_direct_response(
                prompt=json.dumps(payload, ensure_ascii=False, default=str))
            data = self._as_json(response[0] if response else "")
        except Exception as error:  # noqa: BLE001 - never lose the follow-up
            _logger.info("Could not draft a reply for %s: %s", self.id, error)
            return fallback_subject, fallback_body
        if not isinstance(data, dict) or not data.get("body_html"):
            return fallback_subject, fallback_body
        return (str(data.get("subject") or fallback_subject)[:200],
                str(data["body_html"]))

    @api.model
    def _reply_agent(self):
        Agent = self.env["ai.agent"]
        param = self.env["ir.config_parameter"].sudo()
        stored = param.get_param("era_ai_manager.chat_reply_agent_id")
        if stored:
            agent = Agent.sudo().browse(int(stored)).exists()
            if agent:
                return agent
        values = {
            "name": _("AI Manager — Chat follow-up"),
            "subtitle": _("Answers a customer whose chat ended badly."),
            "system_prompt": REPLY_PROMPT,
            "response_style": "balanced",
        }
        working = Agent.sudo().search([("active", "=", True)], order="id", limit=1)
        if working:
            for field in ("llm_model", "era_account_id", "era_model_id"):
                if field in Agent._fields and working[field]:
                    value = working[field]
                    values[field] = value.id if hasattr(value, "id") else value
        agent = Agent.sudo().create(values)
        param.set_param("era_ai_manager.chat_reply_agent_id", str(agent.id))
        return agent

    def _queue_reply(self):
        """Put an answer in the outreach queue.

        Through the queue, never straight to mail.mail: the send window, the
        opt-out and the audit trail are the queue's job, and a message that
        skips it is a message nobody can hold to account. The play is 'reply'
        because they wrote to us first — answering a question is not
        solicitation and is not capped like one.

        When the follow-up is a ticket the answer is posted inside it, so the
        customer's reply comes back to the same thread instead of starting a
        conversation nobody owns.
        """
        self.ensure_one()
        address = self._reply_address()
        if not address:
            return self.env["era.ai.outreach"]
        Outreach = self.env["era.ai.outreach"].sudo()
        existing = Outreach.search([
            ("thread_model", "=", "era.ai.conversation"),
            ("thread_id", "=", self.id),
        ], limit=1)
        if existing:
            return existing  # answered once is enough
        subject, body = self._draft_reply()
        target = self._result_record()
        in_thread = bool(target) and hasattr(target, "message_post")
        return Outreach.create({
            "subject": subject,
            "body_html": body,
            "lang": self.env["era.ai.profile"].owner_language(),
            "channel": "thread_reply" if in_thread else "email",
            "play": "reply",
            "partner_id": self.partner_id.id or False,
            "email_to": address,
            "thread_model": target._name if in_thread else "era.ai.conversation",
            "thread_id": target.id if in_thread else self.id,
            "agent_name": _("Chat follow-up"),
            "rationale": _("Their chat ended without an answer. %s",
                           self.interest or ""),
        })

    def action_reply(self):
        """Answer this customer, creating the follow-up record if needed."""
        for record in self:
            if not record._reply_address():
                raise UserError(_(
                    "There is no email address to answer on. This conversation "
                    "left only a phone number."))
            if record.state != "converted":
                record.action_convert()
            record._queue_reply()
        return True

    def action_ignore(self):
        self.sudo().write({"state": "ignored"})
        return True

    def action_open_source(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.thread_model,
            "res_id": self.thread_id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_result(self):
        self.ensure_one()
        target = self._result_record()
        if not target:
            raise UserError(_("Nothing has been created from this yet."))
        return {
            "type": "ir.actions.act_window",
            "res_model": target._name,
            "res_id": target.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # The cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_harvest(self):
        """Read what is new, and file what can be filed.

        Auto-conversion is limited to visitors who left contact details: a
        conversation with nobody behind it becomes a record to read, never a
        lead pointing at no one.
        """
        harvested = self.browse()
        for channel in self._harvestable_channels():
            try:
                with self.env.cr.savepoint():
                    harvested |= self._harvest_one(channel)
            except Exception as error:  # noqa: BLE001 - one odd chat is not an outage
                _logger.warning("Could not harvest channel %s: %s",
                                channel.id, error)
        for record in harvested:
            if record.kind == "noise":
                continue
            try:
                with self.env.cr.savepoint():
                    record.action_convert()
            except Exception as error:  # noqa: BLE001
                _logger.info("Could not file conversation %s: %s",
                             record.id, error)
                # No lead, no ticket — but an address is an address, and the
                # customer is still owed an answer.
                if record._reply_address():
                    try:
                        with self.env.cr.savepoint():
                            record._queue_reply()
                    except Exception as second:  # noqa: BLE001
                        _logger.info("Could not answer %s: %s", record.id, second)
        return len(harvested)

from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Plays that are marketing/lifecycle in nature. Replies to a customer who wrote
# to us first are NOT marketing: they are exempt from the frequency cap, the
# quiet-hours window and the opt-out check (answering a support question is not
# solicitation, and refusing to answer would be worse service, not better).
# Answering someone who wrote to us first is not solicitation: replies are
# exempt from the frequency cap, the quiet hours and the opt-out check, because
# refusing to answer a question would be worse service, not better. Everything
# else the manager sends is treated as marketing and fully guardrailed. The
# split is by play name so a business can invent its own plays without
# accidentally inventing its own exemption.
REPLY_PLAYS = ("reply",)

# Python's weekday(): Monday is 0, Sunday is 6. Names are accepted in the
# parameter because "6,0,1,2,3" is the Saudi working week written in a way
# nobody can read back, and a send window nobody can read is a send window
# nobody will correct.
WEEKDAY_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


class EraAiOutreach(models.Model):
    """Every customer-facing message the AI produces passes through here.

    The agents write drafts; this model decides whether anything actually
    leaves the building. Guardrails live in Python because a prompt is not an
    enforcement mechanism — an agent that misreads its instructions still
    cannot exceed the cap, mail an opted-out contact, or send at 3am.
    """

    _name = "era.ai.outreach"
    _description = "Era AI Outreach"
    _order = "create_date desc, id desc"
    _inherit = ["mail.thread"]
    _rec_name = "subject"

    subject = fields.Char(required=True, tracking=True)
    body_html = fields.Html(required=True, sanitize=True)
    lang = fields.Selection(
        [("ar_001", "Arabic"), ("en_US", "English")],
        required=True,
        default="ar_001",
    )
    channel = fields.Selection(
        [
            ("email", "Email"),
            ("thread_reply", "Reply in the record"),
        ],
        required=True,
        default="email",
        index=True,
    )
    play = fields.Char(
        required=True,
        index=True,
        help="The kind of message this is. Free text so each business can name "
             "its own plays; 'reply' is reserved for answering someone who "
             "wrote to us first and is exempt from the marketing guardrails.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("pending", "Pending approval"),
            ("approved", "Approved"),
            ("sent", "Sent"),
            ("rejected", "Rejected"),
            ("blocked", "Blocked"),
        ],
        default="draft",
        required=True,
        index=True,
        tracking=True,
    )
    block_reason = fields.Char(readonly=True, tracking=True)
    rationale = fields.Text(
        help="Why the agent proposed this. Recorded for the owner's audit, "
             "never sent to the customer."
    )
    agent_name = fields.Char(index=True, help="Which AI agent produced this draft.")

    partner_id = fields.Many2one("res.partner", ondelete="cascade", index=True)
    # A reply can target any mail.thread record — a helpdesk ticket, a lead, a
    # task, an order. Storing model+id instead of typed columns is what keeps
    # this module installable on a database that has none of those apps.
    thread_model = fields.Char(string="Related Model", index=True)
    thread_id = fields.Integer(string="Related Record")
    thread_ref = fields.Char(compute="_compute_thread_ref", string="Related")
    watchlist_id = fields.Many2one(
        "era.ai.watchlist", ondelete="set null", index=True,
        help="The audience this message came from.",
    )

    email_to = fields.Char(compute="_compute_email_to", store=True, readonly=False)
    sent_at = fields.Datetime(readonly=True, copy=False)
    mail_id = fields.Many2one("mail.mail", readonly=True, copy=False)

    @api.depends("partner_id", "thread_model", "thread_id")
    def _compute_email_to(self):
        for record in self:
            if record.email_to:
                continue
            record.email_to = record.partner_id.email or record._thread_email() or False

    def _thread_email(self):
        """Best-effort recipient from the related record, whatever model it is."""
        self.ensure_one()
        thread = self._thread_record()
        if not thread:
            return False
        for field in ("email_from", "partner_email", "email", "email_normalized"):
            if field in thread._fields and thread[field]:
                return thread[field]
        partner = thread.partner_id if "partner_id" in thread._fields else False
        return partner.email if partner else False

    def _thread_record(self):
        self.ensure_one()
        if not self.thread_model or not self.thread_id:
            return False
        model = self.env.get(self.thread_model)
        if model is None:
            return False
        record = model.browse(self.thread_id).exists()
        return record or False

    @api.depends("thread_model", "thread_id")
    def _compute_thread_ref(self):
        for record in self:
            thread = record._thread_record()
            record.thread_ref = thread.display_name if thread else False

    def action_open_thread(self):
        self.ensure_one()
        if not self._thread_record():
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.thread_model,
            "res_id": self.thread_id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------
    # Autonomy mode
    # ------------------------------------------------------------------
    @api.model
    def _autonomy_mode(self):
        """'ramp' = the owner approves everything; 'full' = send on green.

        The flip is date-driven so the owner never has to remember to do it.
        """
        param = self.env["ir.config_parameter"].sudo()
        mode = param.get_param("era_ai_manager.autonomy_mode", "ramp")
        if mode == "full":
            return "full"
        ramp_end = param.get_param("era_ai_manager.ramp_end_date")
        if ramp_end:
            try:
                if fields.Date.to_date(ramp_end) <= fields.Date.context_today(self):
                    param.set_param("era_ai_manager.autonomy_mode", "full")
                    return "full"
            except (TypeError, ValueError):
                pass
        return "ramp"

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------
    def _guardrail_failure(self):
        """Return the reason this must not be sent, or False when clear.

        Deliberately ordered cheapest/most-absolute first.
        """
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo()

        if not self.email_to:
            return _("No recipient address.")
        if not self.body_html or not self.body_html.strip():
            return _("Empty message body.")

        marketing = self._is_marketing()

        if marketing:
            # Blacklist / opt-out. PDPL and plain decency: an unsubscribe must
            # hold even when the AI has a very good reason to write.
            blacklisted = self.env["mail.blacklist"].sudo().search_count(
                [("email", "=ilike", self.email_to.strip()), ("active", "=", True)]
            )
            if blacklisted:
                return _("Recipient is on the blacklist.")
            if self.partner_id and getattr(self.partner_id, "is_blacklisted", False):
                return _("Recipient has opted out.")

            # Frequency cap: one marketing touch per contact per N days.
            try:
                cap_days = int(param.get_param("era_ai_manager.cap_days", "7"))
            except (TypeError, ValueError):
                cap_days = 7
            recent = self._recent_sent(cap_days)
            if recent:
                return _(
                    "Frequency cap: already contacted on %s.",
                    fields.Datetime.to_string(recent.sent_at),
                )

            # Deduplication: never repeat the same play within N days, even if
            # the cap window has passed.
            try:
                dedup_days = int(param.get_param("era_ai_manager.dedup_days", "30"))
            except (TypeError, ValueError):
                dedup_days = 30
            dedup_days = self.watchlist_id.cooldown_days or dedup_days
            if self._recent_sent(dedup_days, same_play=True):
                return _("Duplicate: '%s' already sent recently.", self.play)

        return False

    def _deferral_reason(self):
        """Conditions that clear on their own — these must never be terminal.

        Kept apart from _guardrail_failure because the two need opposite
        handling. A blacklisted address is still blacklisted tomorrow, but an
        agent that happens to run at 02:00 should have its drafts *wait* for
        the window, not thrown away: treating the two alike silently discarded
        a whole night of the retention agent's work.
        """
        self.ensure_one()
        if self.env.context.get("era_ai_force_send"):
            return False  # the owner pressed Send now: that is a decision
        if self._is_marketing() and not self._within_send_window():
            return _("Waiting for the send window (%s).",
                     self._send_window_label())
        return False

    def _is_marketing(self):
        """Anything that is not answering an inbound message."""
        self.ensure_one()
        return self.play not in REPLY_PLAYS

    def _recent_sent(self, days, same_play=False):
        """Most recent sent outreach to the same person within `days`."""
        self.ensure_one()
        if not self.partner_id:
            return self.browse()
        domain = [
            ("id", "!=", self.id),
            ("state", "=", "sent"),
            ("play", "not in", list(REPLY_PLAYS)),
            ("sent_at", ">=", fields.Datetime.now() - timedelta(days=days)),
        ]
        if same_play:
            domain.append(("play", "=", self.play))
        domain.append(("partner_id", "=", self.partner_id.id))
        return self.sudo().search(domain, order="sent_at desc", limit=1)

    def _send_hours(self):
        param = self.env["ir.config_parameter"].sudo()
        try:
            start = int(param.get_param("era_ai_manager.send_hour_start", "10"))
            end = int(param.get_param("era_ai_manager.send_hour_end", "16"))
        except (TypeError, ValueError):
            return 10, 16
        if not 0 <= start < end <= 24:
            return 10, 16  # a window that cannot open is a bug, not a policy
        return start, end

    def _send_days(self):
        """The weekdays marketing may go out on, as Python weekday numbers.

        Hours alone were never a working week: a nine-to-six window still
        posts on Friday, which in Saudi Arabia is the weekend, and a message
        that lands on a closed business is a message read on Sunday at best
        and resented at worst.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_ai_manager.send_days", "sun,mon,tue,wed,thu")
        days = set()
        for token in (raw or "").split(","):
            token = token.strip().lower()
            if not token:
                continue
            if token[:3] in WEEKDAY_NAMES:
                days.add(WEEKDAY_NAMES[token[:3]])
            elif token.isdigit() and 0 <= int(token) <= 6:
                days.add(int(token))
        # An unreadable or empty setting must not mean "send every day":
        # failing open here would push a marketing blast out on a Friday.
        return days or {6, 0, 1, 2, 3}

    def _local_now(self):
        param = self.env["ir.config_parameter"].sudo()
        tz_name = param.get_param("era_ai_manager.timezone", "Asia/Riyadh")
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone("Asia/Riyadh")
        return pytz.utc.localize(fields.Datetime.now()).astimezone(tz)

    def _within_send_window(self):
        """Working hours on a working day, in the business's timezone."""
        local = self._local_now()
        if local.weekday() not in self._send_days():
            return False
        start, end = self._send_hours()
        return start <= local.hour < end

    def _send_window_label(self):
        """Human wording for the block reason, so the owner sees the rule."""
        start, end = self._send_hours()
        order = [6, 0, 1, 2, 3, 4, 5]  # Sunday first, as the week runs here
        labels = {
            6: _("Sunday"), 0: _("Monday"), 1: _("Tuesday"), 2: _("Wednesday"),
            3: _("Thursday"), 4: _("Friday"), 5: _("Saturday"),
        }
        days = [labels[d] for d in order if d in self._send_days()]
        return _("%(days)s, %(start)02d:00-%(end)02d:00",
                 days=", ".join(days), start=start, end=end)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        """Draft -> pending, or straight to approved in full autonomy."""
        full = self._autonomy_mode() == "full"
        for record in self.filtered(lambda r: r.state == "draft"):
            failure = record._guardrail_failure()
            if failure:
                record.write({"state": "blocked", "block_reason": failure})
                continue
            record.write({"state": "approved" if full else "pending"})
        return True

    def action_approve(self):
        for record in self.filtered(lambda r: r.state in ("draft", "pending")):
            # Re-check at approval time: hours pass between drafting and the
            # owner clicking, and an opt-out may have landed in between.
            failure = record._guardrail_failure()
            if failure:
                record.write({"state": "blocked", "block_reason": failure})
                continue
            record.write({"state": "approved"})
        return True

    def action_reject(self):
        return self.filtered(lambda r: r.state in ("draft", "pending")).write(
            {"state": "rejected"}
        )

    def action_send(self):
        """Explicit send button. The owner asked for it now, so the send
        window does not apply — but the hard guardrails still do."""
        forced = self.with_context(era_ai_force_send=True)
        forced.action_approve()
        return forced.filtered(lambda r: r.state == "approved")._deliver()

    def _deliver(self):
        """Actually put the message on the wire."""
        for record in self:
            if record.state != "approved":
                continue
            if record._deferral_reason():
                continue  # stay approved; the queue cron retries later
            failure = record._guardrail_failure()
            if failure:
                record.write({"state": "blocked", "block_reason": failure})
                continue
            try:
                if record.channel == "thread_reply":
                    record._deliver_thread_reply()
                else:
                    record._deliver_email()
            except Exception as error:  # noqa: BLE001 - one bad row must not stop the queue
                record.write({"state": "blocked", "block_reason": str(error)[:200]})
                continue
            record.write({"state": "sent", "sent_at": fields.Datetime.now()})

        return True

    def _mail_from(self):
        return (
            self.env["ir.config_parameter"].sudo().get_param(
                "era_ai_manager.mail_from", ""
            )
        )

    def _deliver_email(self):
        self.ensure_one()
        mail = self.env["mail.mail"].sudo().create({
            "subject": self.subject,
            "body_html": self.body_html,
            "email_to": self.email_to,
            "email_from": self._mail_from(),
            "reply_to": self._mail_from(),
            "auto_delete": False,
            "recipient_ids": [(6, 0, self.partner_id.ids)] if self.partner_id else False,
        })
        mail.send()
        self.mail_id = mail.id
        if self.partner_id:
            # The owner should be able to reconstruct the relationship from the
            # contact alone, without opening the queue.
            self.partner_id.sudo().message_post(
                body=self.body_html,
                subject=self.subject,
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    def _deliver_thread_reply(self):
        """Reply inside the existing thread so the conversation stays whole."""
        self.ensure_one()
        record = self._thread_record()
        if not record:
            raise UserError(_("A reply needs a related record to answer in."))
        if not hasattr(record, "message_post"):
            raise UserError(
                _("%s does not support messaging.", self.thread_model))
        record.sudo().message_post(
            body=self.body_html,
            subject=self.subject,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            email_from=self._mail_from(),
            partner_ids=self.partner_id.ids,
        )

    # ------------------------------------------------------------------
    # Crons
    # ------------------------------------------------------------------
    @api.model
    def _cron_process_queue(self):
        """Submit drafts, then deliver whatever is approved.

        In the owner's language, because block_reason is written here and then
        read by the owner in the queue. A blocked message whose reason is in
        the wrong language is a message they cannot act on.
        """
        lang = self.env["era.ai.profile"].owner_language()
        if self.env.context.get("lang") != lang:
            return self.with_context(lang=lang)._cron_process_queue()
        self.search([("state", "=", "draft")]).action_submit()
        self.search([("state", "=", "approved")], limit=200)._deliver()
        return True

    @api.model
    def _cron_pending_digest(self):
        """One daily email listing what is waiting on the owner.

        Only sent during the ramp, and only when something is actually
        pending — an empty daily email trains people to ignore it.

        Rendered in the owner's language, not the cron's. These crons run as
        OdooBot, so every _() in here resolved against OdooBot's English while
        the owner reads Arabic — the Arabic translations existed and were
        simply never selected.
        """
        lang = self.env["era.ai.profile"].owner_language()
        if self.env.context.get("lang") != lang:
            return self.with_context(lang=lang)._cron_pending_digest()
        # Approvals only exist while ramping; standing faults exist always.
        # Reporting only the first meant that in full autonomy — the mode the
        # owner is meant to end up in — no routine email arrived at all, and a
        # broken agent stayed broken in silence.
        ramping = self._autonomy_mode() != "full"
        pending = self.search([("state", "=", "pending")], order="create_date") \
            if ramping else self.browse()
        faults = self.env["era.ai.watchdog.alert"].open_summary_html()
        # What actually went out. Under full autonomy nothing stops for
        # approval, so without this the owner has handed over the work and
        # hears nothing back — and the point of handing it over was to stop
        # watching, not to stop knowing.
        sent = self.search([
            ("state", "=", "sent"),
            ("sent_at", ">=", fields.Datetime.now() - timedelta(days=1)),
        ], order="sent_at")
        if not pending and not faults and not sent:
            return True  # a report about nothing teaches people to ignore it
        profile = self.env["era.ai.profile"]
        items = []
        for record in pending:
            who = (record.partner_id.display_name or record.email_to
                   or self.env._("an unknown contact"))
            items.append(
                "<li style='margin-bottom:6px'>%s</li>" % self.env._(
                    "To <b>%(who)s</b> — “%(subject)s” — %(link)s",
                    who=who,
                    subject=record.subject or "",
                    link=profile._record_link(record, self.env._("read it")),
                ))
        sections = []
        if sent:
            rows = []
            for record in sent:
                who = (record.partner_id.display_name or record.email_to
                       or self.env._("an unknown contact"))
                rows.append(
                    "<li style='margin-bottom:6px'>%s</li>" % self.env._(
                        "To <b>%(who)s</b> — “%(subject)s” — %(link)s",
                        who=who, subject=record.subject or "",
                        link=profile._record_link(
                            record, self.env._("read it"))))
            sections += [
                "<h3 style='margin:0 0 6px'>%s</h3>" % self.env._(
                    "Sent in the last 24 hours"),
                "<p>%s</p>" % self.env._(
                    "%s message(s) went out. Nothing here needs you — it is "
                    "so you know what was said in your name.", len(sent)),
                "<ul style='font-size:14px'>%s</ul>" % "".join(rows),
            ]
        if pending:
            sections += [
                "<p>%s</p>" % self.env._(
                    "%s message(s) are waiting for you. None of them has been "
                    "sent, and none will be until you approve it.", len(pending)),
                "<ul style='font-size:14px'>%s</ul>" % "".join(items),
                "<p>%s</p>" % self.env._(
                    "%s to approve or reject them together.",
                    profile._action_link("era_ai_manager.action_era_ai_outreach",
                                         self.env._("Open the outreach queue"))),
                "<p style='color:#6b7785;font-size:13px'>%s</p>" % self.env._(
                    "If you do nothing they simply keep waiting — nobody is "
                    "contacted. This part only arrives while you are in Ramp "
                    "mode."),
            ]
        elif faults and not sent:
            sections.append("<p>%s</p>" % self.env._(
                "Nothing needs approving. These are still broken, though:"))
        sections.append(faults)
        body = "".join(sections)
        recipient = self.env["ir.config_parameter"].sudo().get_param(
            "era_ai_manager.owner_email"
        ) or self.env.ref("base.user_admin").email
        if not recipient:
            return True
        parts = []
        if sent:
            parts.append(self.env._("%s sent", len(sent)))
        if pending:
            parts.append(self.env._("%s awaiting approval", len(pending)))
        if faults:
            parts.append(self.env._("%s still broken", faults.count("<li")))
        subject = self.env._("AI Manager: %s", ", ".join(parts))
        self.env["mail.mail"].sudo().create({
            "subject": subject,
            "body_html": body,
            "email_to": recipient,
            "email_from": self._mail_from(),
            "auto_delete": False,
        }).send()
        return True

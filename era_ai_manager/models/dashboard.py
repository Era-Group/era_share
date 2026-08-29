from datetime import timedelta

from odoo import _, api, fields, models


class EraAiDashboard(models.TransientModel):
    """One screen a general manager can read in a minute and act on.

    Deliberately not a wall of charts. The questions a manager actually has
    are: is it running, what did it do, did any of it arrive, who is it
    reaching, and what still needs me. Everything here answers one of those
    five and links to the records behind it — a number nobody can open is a
    number nobody can check.

    Transient because it is a view of live data, not a record of anything.
    """

    _name = "era.ai.dashboard"
    _description = "Era AI Executive Dashboard"

    # --- is it running -------------------------------------------------
    agents_active = fields.Integer(readonly=True)
    agents_total = fields.Integer(readonly=True)
    last_agent_run = fields.Datetime(readonly=True)
    agent_failures = fields.Integer(readonly=True)
    faults_critical = fields.Integer(readonly=True)
    faults_warning = fields.Integer(readonly=True)
    assistant_failures = fields.Integer(readonly=True)

    # --- what it did ---------------------------------------------------
    sent_period = fields.Integer(readonly=True)
    replies_period = fields.Integer(readonly=True)
    marketing_period = fields.Integer(readonly=True)
    blocked_period = fields.Integer(readonly=True)
    blocked_reason = fields.Char(readonly=True)
    pending_now = fields.Integer(readonly=True)
    conversations_period = fields.Integer(readonly=True)
    converted_period = fields.Integer(readonly=True)

    # --- did it arrive -------------------------------------------------
    delivered_period = fields.Integer(readonly=True)
    bounced_period = fields.Integer(readonly=True)
    failed_period = fields.Integer(readonly=True)

    # --- who it reaches ------------------------------------------------
    watchlists_approved = fields.Integer(readonly=True)
    audience_size = fields.Integer(readonly=True)
    contacts_reachable = fields.Integer(readonly=True)
    reached_ever = fields.Integer(readonly=True)
    opted_out = fields.Integer(readonly=True)

    # --- how it is set to behave ---------------------------------------
    autonomy_label = fields.Char(readonly=True)
    window_label = fields.Char(readonly=True)
    window_open = fields.Boolean(readonly=True)

    period_days = fields.Integer(default=30)
    period_label = fields.Char(readonly=True)
    verdict_html = fields.Html(readonly=True, sanitize=False)

    # ------------------------------------------------------------------
    @api.model
    def open_dashboard(self):
        """Always a fresh reading, in the language the owner reads."""
        lang = self.env["era.ai.profile"].owner_language()
        record = self.with_context(lang=lang).create({})
        record._measure()
        return {
            "type": "ir.actions.act_window",
            "res_model": "era.ai.dashboard",
            "res_id": record.id,
            "view_mode": "form",
            "target": "current",
            "name": self.env._("How the AI manager is doing"),
            "context": dict(self.env.context, lang=lang),
        }

    def action_refresh(self):
        self.ensure_one()
        self._measure()
        return {
            "type": "ir.actions.act_window",
            "res_model": "era.ai.dashboard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _measure(self):
        self.ensure_one()
        now = fields.Datetime.now()
        since = now - timedelta(days=self.period_days or 30)
        Outreach = self.env["era.ai.outreach"].sudo()
        values = {"period_label": _("the last %s days", self.period_days or 30)}

        # --- is it running
        scheduled = self.env.get("aidoo.scheduled")
        if scheduled is not None:
            agents = scheduled.with_context(active_test=False).sudo()
            values["agents_total"] = agents.search_count([])
            values["agents_active"] = agents.search_count([("active", "=", True)])
        session = self.env.get("aidoo.session")
        if session is not None:
            done = session.sudo().search(
                [("state", "=", "done")], order="create_date desc", limit=1)
            values["last_agent_run"] = done.create_date or False
            values["agent_failures"] = session.sudo().search_count(
                [("state", "=", "error"), ("create_date", ">=", since)])
        Alert = self.env["era.ai.watchdog.alert"].sudo()
        values["faults_critical"] = Alert.search_count(
            [("state", "=", "open"), ("severity", "=", "critical")])
        values["faults_warning"] = Alert.search_count(
            [("state", "=", "open"), ("severity", "=", "warning")])
        values["assistant_failures"] = self.env[
            "era.ai.conversation"].count_broken_conversations(days=7)

        # --- what it did
        sent = Outreach.search([("state", "=", "sent"), ("sent_at", ">=", since)])
        values["sent_period"] = len(sent)
        values["replies_period"] = len(sent.filtered(
            lambda r: not r._is_marketing()))
        values["marketing_period"] = values["sent_period"] - values["replies_period"]
        blocked = Outreach.search(
            [("state", "=", "blocked"), ("create_date", ">=", since)])
        values["blocked_period"] = len(blocked)
        values["blocked_reason"] = self._commonest(
            blocked.mapped("block_reason"))
        values["pending_now"] = Outreach.search_count([("state", "=", "pending")])
        Conversation = self.env["era.ai.conversation"].sudo()
        values["conversations_period"] = Conversation.search_count(
            [("create_date", ">=", since)])
        values["converted_period"] = Conversation.search_count(
            [("create_date", ">=", since), ("state", "=", "converted")])

        # --- did it arrive. Counted from the mail itself, because "we sent
        #     it" and "it landed" are different claims and only the second one
        #     is worth a manager's attention.
        mails = sent.mapped("mail_id")
        values["delivered_period"] = len(mails.filtered(
            lambda m: m.state == "sent"))
        values["failed_period"] = len(mails.filtered(
            lambda m: m.state == "exception"))
        notifications = self.env["mail.notification"].sudo().search_count([
            ("notification_status", "=", "bounce"),
            ("res_partner_id", "in", sent.mapped("partner_id").ids),
        ]) if sent.mapped("partner_id") else 0
        values["bounced_period"] = notifications

        # --- who it reaches
        Watchlist = self.env["era.ai.watchlist"].sudo()
        approved = Watchlist.search([("state", "=", "approved")])
        values["watchlists_approved"] = len(approved)
        values["audience_size"] = sum(w.match_count for w in approved)
        Partner = self.env["res.partner"].sudo()
        values["contacts_reachable"] = Partner.search_count(
            [("email", "!=", False)])
        values["reached_ever"] = len(set(
            Outreach.search([("state", "=", "sent")]).mapped("partner_id").ids))
        if "is_blacklisted" in Partner._fields:
            values["opted_out"] = Partner.search_count(
                [("is_blacklisted", "=", True)])

        # --- how it is set to behave
        full = Outreach._autonomy_mode() == "full"
        values["autonomy_label"] = (
            _("Full autonomy — messages go out without you")
            if full else _("Ramp — you approve every message"))
        values["window_label"] = Outreach._send_window_label()
        values["window_open"] = Outreach._within_send_window()

        self.write(values)
        self.write({"verdict_html": self._render_verdict()})
        return True

    @api.model
    def _commonest(self, values):
        cleaned = [v for v in values if v]
        if not cleaned:
            return False
        return max(set(cleaned), key=cleaned.count)[:120]

    def _render_verdict(self):
        """The paragraph a manager reads first, and sometimes only.

        Leads with whatever is worst, because a summary that opens with good
        news when something is broken is a summary that trained its reader to
        skip it.
        """
        self.ensure_one()
        profile = self.env["era.ai.profile"]
        bad, fine = [], []

        if self.faults_critical:
            bad.append(self.env._(
                "%(count)s thing(s) are broken right now and affecting "
                "customers. %(link)s",
                count=self.faults_critical,
                link=profile._action_link(
                    "era_ai_manager.action_era_ai_watchdog",
                    self.env._("See what"))))
        if self.assistant_failures:
            bad.append(self.env._(
                "The website assistant showed a technical error to "
                "%s visitor(s) in the last week.", self.assistant_failures))
        if self.agents_total and not self.agents_active:
            bad.append(self.env._(
                "None of the %s AI staff is switched on, so nothing is being "
                "watched or written.", self.agents_total))
        elif self.agents_total and self.agents_active < self.agents_total:
            fine.append(self.env._(
                "%(on)s of %(all)s AI staff are switched on.",
                on=self.agents_active, all=self.agents_total))
        if self.pending_now:
            bad.append(self.env._(
                "%(count)s message(s) are waiting for your approval and will "
                "not go anywhere until you look. %(link)s",
                count=self.pending_now,
                link=profile._action_link(
                    "era_ai_manager.action_era_ai_outreach",
                    self.env._("Review them"))))
        if self.failed_period or self.bounced_period:
            bad.append(self.env._(
                "%(failed)s message(s) could not be delivered and "
                "%(bounced)s bounced.",
                failed=self.failed_period, bounced=self.bounced_period))

        if self.sent_period:
            fine.append(self.env._(
                "%(total)s message(s) went out in %(period)s — %(marketing)s "
                "reaching out, %(replies)s answering someone who wrote first.",
                total=self.sent_period, period=self.period_label,
                marketing=self.marketing_period, replies=self.replies_period))
        else:
            bad.append(self.env._(
                "Nothing has been sent in %s. Either nobody needed "
                "contacting, or the manager is not running.", self.period_label))
        if self.converted_period:
            fine.append(self.env._(
                "%(done)s of %(read)s chat(s) read became a lead or a ticket.",
                done=self.converted_period, read=self.conversations_period))
        if self.audience_size:
            fine.append(self.env._(
                "%(audience)s customer(s) are under watch across "
                "%(lists)s audience(s).",
                audience=self.audience_size, lists=self.watchlists_approved))

        def block(title, items, colour):
            if not items:
                return ""
            return (
                "<div style='margin-bottom:10px'>"
                "<div style='font-weight:600;color:%s;margin-bottom:4px'>%s</div>"
                "<ul style='margin:0;padding-inline-start:18px'>%s</ul></div>" % (
                    colour, title,
                    "".join("<li style='margin-bottom:3px'>%s</li>" % i
                            for i in items)))

        return (
            block(self.env._("Needs you"), bad, "#b02a37")
            + block(self.env._("Running as intended"), fine, "#1a7f4b")
            + "<div style='color:#6b7785;font-size:12px;margin-top:6px'>%s</div>" % (
                self.env._(
                    "Deliveries are counted from the mail server's own answer. "
                    "Opens and clicks are not tracked, so this page will never "
                    "claim to know whether a message was read."))
        )

    # ------------------------------------------------------------------
    # Drill-downs: every number here opens the records behind it.
    # ------------------------------------------------------------------
    def _open(self, model, name, domain):
        return {
            "type": "ir.actions.act_window", "res_model": model, "name": name,
            "view_mode": "list,form", "domain": domain, "target": "current",
        }

    def action_open_sent(self):
        since = fields.Datetime.now() - timedelta(days=self.period_days or 30)
        return self._open("era.ai.outreach", _("Sent"),
                          [("state", "=", "sent"), ("sent_at", ">=", since)])

    def action_open_pending(self):
        return self._open("era.ai.outreach", _("Waiting for you"),
                          [("state", "=", "pending")])

    def action_open_blocked(self):
        since = fields.Datetime.now() - timedelta(days=self.period_days or 30)
        return self._open("era.ai.outreach", _("Blocked by a guardrail"),
                          [("state", "=", "blocked"), ("create_date", ">=", since)])

    def action_open_faults(self):
        return self._open("era.ai.watchdog.alert", _("Still broken"),
                          [("state", "=", "open")])

    def action_open_conversations(self):
        return self._open("era.ai.conversation", _("Conversations read"), [])

    def action_open_watchlists(self):
        return self._open("era.ai.watchlist", _("Audiences"),
                          [("state", "=", "approved")])

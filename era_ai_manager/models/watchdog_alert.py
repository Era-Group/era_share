from datetime import timedelta

from odoo import _, api, fields, models


class EraAiWatchdogAlert(models.Model):
    """Deterministic health checks over the whole automated pipeline.

    Everything here is plain SQL/ORM counting — no AI. If the AI stack itself
    is broken, this still runs and still reaches the owner, which is the whole
    point: the watchdog must not depend on the thing it watches.
    """

    _name = "era.ai.watchdog.alert"
    _description = "Era AI Watchdog Alert"
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, index=True)
    check_key = fields.Char(required=True, index=True)
    severity = fields.Selection(
        [("warning", "Warning"), ("critical", "Critical")],
        default="warning",
        required=True,
        index=True,
    )
    detail = fields.Text()
    state = fields.Selection(
        [("open", "Open"), ("resolved", "Resolved")],
        default="open",
        required=True,
        index=True,
    )
    resolved_at = fields.Datetime(readonly=True)

    @api.model
    def _run_checks(self):
        """Return [(check_key, name, severity, detail)] for everything wrong."""
        findings = []
        now = fields.Datetime.now()
        param = self.env["ir.config_parameter"].sudo()

        # 1. Is any audience defined at all? An unconfigured manager looks
        #    exactly like a healthy one: silent.
        watchlists = self.env["era.ai.watchlist"].search([])
        if not watchlists:
            findings.append((
                "no_watchlists",
                _("The manager has no audience to watch"),
                "warning",
                _("Run the business survey, or add watchlists by hand, so the "
                  "manager knows which customers to look after."),
            ))

        # 2. Outgoing mail. Everything else in this module is worthless if mail
        #    silently fails.
        failed_mail = self.env["mail.mail"].sudo().search_count(
            [("state", "=", "exception")]
        )
        if failed_mail:
            findings.append((
                "mail_exception",
                _("%s outgoing email(s) failed", failed_mail),
                "critical",
                _("Check Settings > Technical > Outgoing Mail Servers."),
            ))

        # 3. AI runs that died. Aidoo delivers nothing and alerts no one when a
        #    scheduled run errors, so this is the only place it surfaces.
        if "aidoo.session" in self.env:
            broken = self.env["aidoo.session"].sudo().search_count([
                ("state", "=", "error"),
                ("scheduled_id", "!=", False),
                ("write_date", ">=", now - timedelta(days=1)),
            ])
            if broken:
                findings.append((
                    "agent_error",
                    _("%s scheduled AI agent run(s) failed", broken),
                    "warning",
                    _("Open Aidoo > Scheduled Agents and review the transcripts."),
                ))

        # 4. Customers waiting on us, in whichever app handles them here.
        try:
            sla_hours = int(param.get_param("era_ai_manager.reply_sla_hours", "8"))
        except (TypeError, ValueError):
            sla_hours = 8
        for model_name, label in (("helpdesk.ticket", _("ticket")),
                                  ("crm.lead", _("lead"))):
            model = self.env.get(model_name)
            if model is None:
                continue
            domain = [("create_date", "<", now - timedelta(hours=sla_hours))]
            if "stage_id" in model._fields:
                domain.append(("stage_id.fold", "=", False))
            try:
                waiting = model.sudo().search_count(domain)
            except Exception:  # noqa: BLE001 - an odd stage model is not an outage
                continue
            if waiting:
                findings.append((
                    "backlog_%s" % model_name.replace(".", "_"),
                    _("%(count)s %(label)s(s) older than %(hours)sh",
                      count=waiting, label=label, hours=sla_hours),
                    "warning",
                    _("The agent handling this may be inactive or blocked."),
                ))

        # 5. The queue itself jamming — a systematic guardrail failure looks
        #    exactly like silence, so count it explicitly.
        blocked = self.env["era.ai.outreach"].sudo().search_count([
            ("state", "=", "blocked"),
            ("create_date", ">=", now - timedelta(days=1)),
        ])
        if blocked >= 5:
            findings.append((
                "outreach_blocked",
                _("%s outreach draft(s) blocked in 24h", blocked),
                "warning",
                _("Review the block reasons — the agents may be mis-targeting."),
            ))

        # 6. The assistant answering the public with a technical error. This
        #    is the worst failure in the system and the quietest: the bot keeps
        #    replying, the logs look busy, and every visitor sees a stack
        #    trace where an answer should be. Nothing else notices.
        broken_chats = self.env["era.ai.conversation"].count_broken_conversations(
            days=7)
        if broken_chats:
            findings.append((
                "assistant_failed",
                _("The chat assistant showed an error to %s visitor(s)",
                  broken_chats),
                "critical",
                _("They saw a technical message instead of an answer. Check "
                  "the assistant's provider key and quota — every one of these "
                  "is a customer who left without being helped."),
            ))

        return findings

    @api.model
    def _cron_watchdog(self):
        """Open new alerts, auto-resolve fixed ones, email the owner once.

        The whole check runs in the owner's language: the alert name and
        detail are stored on the record and read by the owner in the list
        view, so translating only the email would leave half of it English.
        """
        lang = self.env["era.ai.profile"].owner_language()
        if self.env.context.get("lang") != lang:
            return self.with_context(lang=lang)._cron_watchdog()
        findings = self._run_checks()
        found_keys = {key for key, _n, _s, _d in findings}

        open_alerts = self.search([("state", "=", "open")])
        # Auto-resolve: no email, no noise — the owner only hears about problems.
        open_alerts.filtered(lambda a: a.check_key not in found_keys).write(
            {"state": "resolved", "resolved_at": fields.Datetime.now()}
        )

        # Refresh what is already open before deciding what is new. The name
        # and detail are rendered text: an alert opened yesterday keeps
        # yesterday's count for ever ("11 emails failed" long after it is 40),
        # and keeps whatever language was resolvable at the moment it was
        # created. Both are wrong by the next morning, and the owner reads
        # this list rather than the code that produced it.
        by_key = {alert.check_key: alert for alert in open_alerts}
        for key, name, severity, detail in findings:
            alert = by_key.get(key)
            if not alert:
                continue
            changed = {}
            if alert.name != name:
                changed["name"] = name
            if alert.detail != detail:
                changed["detail"] = detail
            if alert.severity != severity:
                changed["severity"] = severity
            if changed:
                alert.write(changed)

        existing = set(open_alerts.mapped("check_key"))
        # A recordset, not a list of records: _notify_owner sorts and filters
        # it to lead with what is actually breaking, and a list only looks like
        # a recordset until something asks it to behave like one.
        fresh = self.browse()
        for key, name, severity, detail in findings:
            if key in existing:
                continue  # already reported; do not re-nag every hour
            fresh |= self.create({
                "check_key": key,
                "name": name,
                "severity": severity,
                "detail": detail,
            })

        if fresh:
            self._notify_owner(fresh)
        return True

    @api.model
    def open_summary_html(self):
        """Everything still broken, for the recurring reports.

        The one-off alert email deliberately reports each problem once, so the
        owner is not nagged hourly. The cost of that rule is silence: an
        assistant that has been failing for a week looks exactly like an
        assistant that was fixed. This is the counterweight — the standing
        problems restated in every routine report, with how long each has been
        open, until it clears.
        """
        alerts = self.sudo().search([("state", "=", "open")])
        if not alerts:
            return ""
        profile = self.env["era.ai.profile"]
        now = fields.Datetime.now()
        items = []
        for alert in alerts.sorted(lambda a: (a.severity != "critical",
                                              a.create_date)):
            # Rounded, not truncated: an alert opened three days ago is
            # nearly always a few seconds short of 3.0, and reporting "2 days"
            # understates neglect in the one report whose subject is neglect.
            days = round((now - alert.create_date).total_seconds() / 86400) \
                if alert.create_date else 0
            if days >= 1:
                age = self.env._("open for %s day(s)", days)
            else:
                age = self.env._("noticed today")
            colour = "#b02a37" if alert.severity == "critical" else "#8a6d3b"
            items.append(
                "<li style='margin-bottom:6px'><b style='color:%s'>%s</b> "
                "<span style='color:#8a94a0'>(%s)</span><br/>"
                "<span style='color:#5b6b7c'>%s</span></li>" % (
                    colour, alert.name, age, alert.detail or ""))
        return "".join([
            "<h3 style='margin:18px 0 6px'>%s</h3>" % self.env._(
                "Still not fixed"),
            "<ul style='font-size:14px'>%s</ul>" % "".join(items),
            "<p>%s</p>" % self.env._(
                "%s to see everything, including what has already cleared.",
                profile._action_link("era_ai_manager.action_era_ai_watchdog",
                                     self.env._("Open the watchdog"))),
        ])

    def _notify_owner(self, alerts):
        """Written in the owner's language, not the cron's.

        The watchdog runs as OdooBot; without this every alert reached an
        Arabic owner in English, because _() resolves against the calling
        user's language and nobody had told it whose language mattered.
        """
        lang = self.env["era.ai.profile"].owner_language()
        if self.env.context.get("lang") != lang:
            return self.with_context(lang=lang)._notify_owner(alerts)
        param = self.env["ir.config_parameter"].sudo()
        recipient = param.get_param("era_ai_manager.owner_email") or self.env.ref(
            "base.user_admin"
        ).email
        if not recipient:
            return
        profile = self.env["era.ai.profile"]
        critical = alerts.filtered(lambda a: a.severity == "critical")
        items = []
        for alert in alerts.sorted(lambda a: a.severity != "critical"):
            items.append(
                "<li style='margin-bottom:8px'><b>%s</b><br/>"
                "<span style='color:#5b6b7c'>%s</span></li>" % (
                    alert.name, alert.detail or ""))
        body = "".join([
            "<p>%s</p>" % (
                self.env._(
                    "Something is broken and it is affecting customers now:")
                if critical else
                self.env._("A few things need looking at. Nothing is broken "
                           "for customers right now:")),
            "<ul style='font-size:14px'>%s</ul>" % "".join(items),
            "<p>%s</p>" % self.env._(
                "%s to see the full list and what has already been resolved.",
                profile._action_link("era_ai_manager.action_era_ai_watchdog",
                                     self.env._("Open the watchdog"))),
            "<p style='color:#6b7785;font-size:13px'>%s</p>" % self.env._(
                "I keep working meanwhile, and I will stop mentioning each of "
                "these once it clears. You only hear about a problem once."),
        ])
        self.env["mail.mail"].sudo().create({
            "subject": self.env._("AI Manager: %s new issue(s)", len(alerts)),
            "body_html": body,
            "email_to": recipient,
            "email_from": param.get_param("era_ai_manager.mail_from", ""),
            "auto_delete": False,
        }).send()

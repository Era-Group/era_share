import json
import logging
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Models worth counting when they exist. Deliberately broad: the point of the
# survey is to find out what kind of business this is, and the answer is mostly
# "which of these actually have rows". Standard Odoo models only — naming any
# one customer's custom model here would defeat the purpose, and the discovery
# agent has introspection tools to find whatever else this database carries.
# Name fragments that mark a table as a record OF something rather than a
# record of someone. Used only to rank, never to exclude: a log can still be
# worth reading, it is just never the customer base.
PLUMBING_HINTS = (
    "log", "message", "event", "history", "track", "queue", "line", "tag",
    "attachment", "session", "token", "audit", "trace", "stat", "report",
)

# Prefixes whose row counts describe Odoo, not the business.
TECHNICAL_PREFIXES = (
    "ir.", "bus.", "base_", "base.", "res.lang", "res.currency", "res.country",
    "res.groups", "res.company", "res.config", "mail.tracking", "mail.followers",
    "mail.notification", "mail.message.subtype", "mail.alias", "mail.render",
    "web_", "website.rewrite", "report.", "format.", "publisher_", "iap.",
    "digest.", "utm.", "link.tracker", "resource.", "decimal.precision",
    "sms.tracker", "auth_", "phone.", "spreadsheet.", "onboarding.",
)

SURVEY_MODELS = [
    ("res.partner", "Contacts", [("is_company", "=", True)]),
    ("crm.lead", "CRM leads", []),
    ("helpdesk.ticket", "Helpdesk tickets", []),
    ("project.task", "Project tasks", []),
    ("sale.order", "Sales orders", []),
    ("purchase.order", "Purchase orders", []),
    ("account.move", "Journal entries", [("move_type", "!=", "entry")]),
    ("product.template", "Products / services", []),
    ("stock.picking", "Deliveries", []),
    ("hr.employee", "Employees", []),
    ("event.event", "Events", []),
    ("calendar.event", "Meetings", []),
    ("mrp.production", "Manufacturing orders", []),
    ("repair.order", "Repair orders", []),
    ("fleet.vehicle", "Vehicles", []),
    ("subscription.package", "Subscriptions", []),
    ("sale.subscription", "Subscriptions", []),
    ("pos.order", "Point of sale orders", []),
    ("website.visitor", "Website visitors", []),
    ("mailing.mailing", "Email campaigns", []),
    ("survey.survey", "Surveys", []),
    ("slide.channel", "Courses", []),
    ("hr.applicant", "Job applicants", []),
    ("account.asset", "Assets", []),
]


# What the manager can actually DO here, decided by which models exist rather
# than by which modules are named. A capability is only claimed when its model
# is really present and queryable, because a brief that promises WhatsApp
# follow-up on a database without WhatsApp is worse than one that never
# mentions it. Each entry: key, label, required models (any of), what it
# enables, and which duty it feeds.
CAPABILITIES = [
    ("support_desk", "Help desk", ["helpdesk.ticket"],
     "Answer inbound tickets and reply inside the ticket thread.", "inbox"),
    ("leads", "CRM pipeline", ["crm.lead"],
     "Qualify inbound enquiries, answer them, and schedule the next follow-up.",
     "inbox"),
    ("email_campaigns", "Email marketing", ["mailing.mailing"],
     "Build and maintain mailing lists and run periodic campaigns.", "campaign"),
    ("livechat", "Live chat", ["im_livechat.channel"],
     "Website visitors can be answered in real time. Their conversations "
     "(discuss.channel, channel_type = livechat) are worth watching for "
     "questions that went unanswered or ended badly, but most visitors are "
     "anonymous guests with no contact record, so treat live chat as a source "
     "of insight and of leads to create, not as an audience to email.", None),
    ("website", "Website", ["website"],
     "There is a public site; visitor behaviour is worth reading.", None),
    ("sales", "Sales orders", ["sale.order"],
     "Reorder cadence, quotation follow-up, and what each customer buys.",
     "followup"),
    ("pos", "Point of sale", ["pos.order"],
     "Retail purchase frequency and lapsed shoppers.", "followup"),
    ("subscriptions", "Subscriptions", ["sale.subscription", "subscription.package"],
     "Renewal windows and churn before it happens.", "followup"),
    ("invoicing", "Invoicing", ["account.move"],
     "Payment status. Treat money with care and escalate disputes.", None),
    ("projects", "Projects", ["project.task"],
     "Delivery progress and work that has stalled.", "followup"),
    ("events", "Events", ["event.event"],
     "Attendee reminders and post-event follow-up.", "followup"),
    ("courses", "eLearning", ["slide.channel"],
     "Learners who enrolled and stopped.", "followup"),
    ("surveys", "Surveys", ["survey.survey"],
     "Ask for satisfaction, and read what came back.", None),
    ("appointments", "Appointments", ["appointment.type"],
     "Booking reminders and no-show follow-up.", "followup"),
    ("repairs", "Repairs", ["repair.order"],
     "Service due, and jobs waiting on the customer.", "followup"),
    ("fleet", "Fleet", ["fleet.vehicle"],
     "Maintenance and renewal dates per vehicle.", "followup"),
    ("recruitment", "Recruitment", ["hr.applicant"],
     "Candidates left waiting.", "followup"),
    ("sms", "SMS", ["sms.sms"],
     "Short reminders where email is too slow.", None),
    ("whatsapp", "WhatsApp", ["whatsapp.message"],
     "WhatsApp follow-up, where the customer already is.", None),
    ("activities", "Activities", ["mail.activity"],
     "Schedule internal to-dos so nothing is silently dropped.", None),
]


class EraAiProfile(models.Model):
    """What the manager knows about this business, and the brief it works from.

    A manager who has to be told everything is not much of a manager. This
    model surveys the database for hard evidence — which apps carry real data,
    how many customers, in what languages, at what tempo — and hands that to
    the AI to turn into a brief, watchlists and playbooks fitted to this
    particular trade. A human reads the result before any of it takes effect.
    """

    _name = "era.ai.profile"
    _description = "Era AI Business Profile"
    _order = "id desc"
    _rec_name = "display_title"

    display_title = fields.Char(compute="_compute_display_title")
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    state = fields.Selection(
        [
            ("new", "Not surveyed"),
            ("surveyed", "Survey collected"),
            ("drafted", "Brief drafted"),
            ("applied", "In effect"),
        ],
        default="new",
        required=True,
        index=True,
    )
    surveyed_at = fields.Datetime(readonly=True)
    applied_at = fields.Datetime(readonly=True)

    # -- evidence (written by Python, never by the AI) -------------------
    evidence_json = fields.Text(
        readonly=True,
        help="Hard facts collected from this database. The AI reads this; it "
             "does not get to edit it.",
    )
    evidence_summary = fields.Text(readonly=True, string="What we found")

    # -- the AI's proposal (written by the discovery agent) --------------
    business_summary = fields.Text(
        string="What this business is",
        help="The manager's own understanding of the trade, in plain words.",
    )
    persona_brief = fields.Text(
        string="Manager brief",
        help="The instructions the manager works from. This is the real "
             "specification of the system — edit it to change behaviour.",
    )
    proposed_watchlists = fields.Text(
        string="Proposed watchlists (JSON)",
        help="Audiences the AI suggests watching. Reviewed, then created.",
    )
    proposed_campaign_days = fields.Integer(
        string="Campaign every (days)",
        default=30,
        help="How often a campaign suits this trade. A busy retailer earns a "
             "weekly note; a firm selling one project a year does not. "
             "Applying retimes the campaign agent to match.",
    )
    recommended_agents = fields.Char(
        readonly=True,
        help="Which agents have something to do here, given what is installed.",
    )
    apply_report = fields.Html(
        string="What happened when you applied",
        readonly=True,
        sanitize=False,
        help="Exactly which proposals landed, which were held and which were "
             "rejected, in plain words and with links to the records. Silently "
             "dropping a suggestion would leave you believing an audience is "
             "covered when it is not.",
    )

    # Written by the survey, never by the agent. The brief tells the AI it does
    # not get to edit the evidence; granting it write access on this model made
    # that a promise the code has to keep rather than a sentence in a prompt.
    EVIDENCE_FIELDS = ("evidence_json", "evidence_summary", "surveyed_at",
                       "applied_at", "apply_report", "recommended_agents")

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group("base.group_system"):
            vals = {key: value for key, value in vals.items()
                    if key not in self.EVIDENCE_FIELDS}
        return super().write(vals)

    @api.depends("company_id", "state")
    def _compute_display_title(self):
        for profile in self:
            profile.display_title = "%s — %s" % (
                profile.company_id.name or _("Business"),
                dict(self._fields["state"].selection).get(profile.state, ""),
            )

    # ------------------------------------------------------------------
    # Step 1 — survey the database (deterministic, no AI)
    # ------------------------------------------------------------------
    @api.model
    def _collect_evidence(self):
        """Facts only. Cheap, repeatable, and true even with no AI configured."""
        company = self.env.company
        Module = self.env["ir.module.module"].sudo()
        apps = Module.search([("state", "=", "installed"), ("application", "=", True)])

        volumes = {}
        for model_name, label, domain in SURVEY_MODELS:
            model = self.env.get(model_name)
            if model is None or not model._auto:
                continue
            try:
                count = model.sudo().search_count(domain)
            except Exception:  # noqa: BLE001 - an unreadable model is simply not evidence
                continue
            if count:
                volumes[model_name] = {"label": label, "count": count}

        Message = self.env["mail.message"].sudo()
        now = fields.Datetime.now()
        from datetime import timedelta

        return {
            "company": {
                "name": company.name,
                "country": company.country_id.name,
                "currency": company.currency_id.name,
                "email": company.email,
                "website": company.website,
                "phone": company.phone,
                "vat": company.vat,
            },
            "languages": self.env["res.lang"].sudo().search([]).mapped("code"),
            # Which of those the person reading the brief actually uses.
            "owner_language": self._owner_language(),
            "installed_apps": sorted(apps.mapped("shortdesc")),
            "record_volumes": volumes,
            # The sweep is what guarantees a custom customer model is seen.
            "data_models": self._discover_data_models(),
            # What the manager may actually do here.
            "capabilities": self._detect_capabilities(),
            "tempo": {
                "messages_last_30_days": Message.search_count(
                    [("create_date", ">=", now - timedelta(days=30))]),
                "messages_last_7_days": Message.search_count(
                    [("create_date", ">=", now - timedelta(days=7))]),
                "internal_users": self.env["res.users"].sudo().search_count(
                    [("share", "=", False), ("active", "=", True)]),
                "contacts_created_last_90_days": self.env["res.partner"].sudo().search_count(
                    [("create_date", ">=", now - timedelta(days=90))]),
            },
            "mail": {
                "aliases": [
                    {"alias": a.alias_name, "model": a.alias_model_id.model}
                    for a in self.env["mail.alias"].sudo().search(
                        [("alias_name", "!=", False)], limit=20)
                ],
                "outgoing_servers": self.env["ir.mail_server"].sudo().search_count([]),
            },
            "existing_watchlists": [
                {"name": w.name, "model": w.model_name, "domain": w.domain,
                 "play": w.play, "matches": w.match_count}
                for w in self.env["era.ai.watchlist"].search([])
            ],
        }

    @api.model
    def owner_language(self):
        """Public accessor: other models write to the owner too."""
        return self._owner_language()

    @api.model
    def _owner_language(self):
        """The language the owner reads, stated rather than left to inference.

        The evidence listed the installed languages and never said which one
        belongs to the reader, so the agent wrote the owner's own brief in the
        language of its instructions — English — for an Arabic business. A
        survey that lists ['ar_001', 'en_US'] answers a different question
        from "which of these is the person reading this".
        """
        param = self.env["ir.config_parameter"].sudo()
        owner_email = param.get_param("era_ai_manager.owner_email")
        if owner_email:
            owner = self.env["res.users"].sudo().search(
                [("email", "=ilike", owner_email)], limit=1)
            if owner.lang:
                return owner.lang
        admin = self.env.ref("base.user_admin", raise_if_not_found=False)
        if admin and admin.lang:
            return admin.lang
        partner = self.env.company.partner_id
        if partner.lang:
            return partner.lang
        installed = self.env["res.lang"].sudo().search([]).mapped("code")
        return installed[0] if installed else "en_US"

    @api.model
    def _detect_capabilities(self):
        """Which channels and workflows this database actually supports.

        Driven off model presence, not module names: a module can be installed
        and its model still absent behind a feature flag, and the manager must
        never promise a channel it cannot reach.
        """
        detected = []
        for key, label, models, enables, duty in CAPABILITIES:
            present = []
            for model_name in models:
                model = self.env.get(model_name)
                if model is None or not model._auto:
                    continue
                try:
                    present.append({"model": model_name,
                                    "rows": model.sudo().search_count([])})
                except Exception:  # noqa: BLE001 - unreadable means unavailable
                    continue
            detected.append({
                "capability": key,
                "label": label,
                "available": bool(present),
                "models": present,
                "in_use": any(entry["rows"] for entry in present),
                "enables": enables,
                "feeds_agent": duty,
            })
        return detected

    @api.model
    def _custom_module_names(self):
        """Modules this deployment added on top of Odoo.

        Odoo authors its own modules; anything else was installed for this
        business specifically, which is exactly the signal we want.
        """
        modules = self.env["ir.module.module"].sudo().search(
            [("state", "=", "installed")])
        return {
            module.name for module in modules
            if not (module.author or "").strip().lower().startswith("odoo")
        }

    @staticmethod
    def _customer_signals(model):
        """Does this model look like it holds customers, or like plumbing?

        Row count alone is a bad guide: an SEO redirect log will out-number the
        customer table a thousand to one and tell you nothing. What marks a
        customer record is that it points at a contact, carries an email, and
        has a date showing when that person was last active — the same three
        traits whether the records are subscribers, patients or vehicles.
        """
        fields = model._fields
        partner_links = [
            name for name, field in fields.items()
            if field.type == "many2one" and field.comodel_name == "res.partner"
        ]
        emails = [name for name in fields if "email" in name]
        # Any stored date beyond the technical ones counts. An allowlist of
        # English hints ("last_", "visit") reads well and quietly fails on the
        # clinic whose field is appointment_date or the gym whose field is
        # checkin_on — and missing the customer base is the one error that
        # cannot be recovered from later.
        recency = [
            name for name, field in fields.items()
            if field.type in ("date", "datetime") and field.store
            and name not in ("create_date", "write_date")
        ]
        looks_like_customers = bool((partner_links or emails) and recency)
        return {
            "partner_links": partner_links[:5],
            "email_fields": emails[:5],
            "recency_fields": recency[:8],
            "looks_like_customers": looks_like_customers,
            "looks_like_plumbing": any(
                hint in model._name for hint in PLUMBING_HINTS),
        }

    @api.model
    def _discover_data_models(self, limit=40):
        """Every stored model that actually carries rows, custom ones first.

        A curated list of standard Odoo models cannot find the heart of a
        business that keeps its customers in a model of its own — and that is
        precisely the case worth getting right, because a custom model with
        rows is usually the product itself. Missing it would have the manager
        conclude a SaaS is a website because website.visitor had the loudest
        row count.
        """
        custom_modules = self._custom_module_names()

        # Which module defines each model, so custom ones can be flagged.
        self.env.cr.execute("""
            SELECT m.model, d.module
              FROM ir_model m
              JOIN ir_model_data d
                ON d.model = 'ir.model' AND d.res_id = m.id
        """)
        owner = dict(self.env.cr.fetchall())

        # Planner estimates are free; use them only to rank the standard
        # models. Custom models are always counted exactly - there are few of
        # them and they are the ones that matter.
        self.env.cr.execute("""
            SELECT c.relname, GREATEST(c.reltuples, 0)::bigint
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'r' AND n.nspname = 'public'
        """)
        estimates = dict(self.env.cr.fetchall())

        candidates = []
        for model_name, module in owner.items():
            if model_name.startswith(TECHNICAL_PREFIXES):
                continue
            model = self.env.get(model_name)
            if model is None or model._transient or not model._auto:
                continue
            is_custom = module in custom_modules
            estimate = estimates.get(model._table, 0)
            if not is_custom and estimate <= 0:
                continue
            signals = self._customer_signals(model)
            # Rank: custom before standard, customer-shaped before plumbing,
            # then by size. Without the middle key a redirect log with 24k rows
            # buries the 30-row table that IS the business.
            candidates.append((
                not is_custom,
                not signals["looks_like_customers"],
                signals["looks_like_plumbing"],
                -estimate, model_name, module, is_custom, signals,
            ))

        candidates.sort()
        found = []
        for __, __, __, __, model_name, module, is_custom, signals in candidates[:limit * 2]:
            model = self.env[model_name]
            try:
                count = model.sudo().search_count([])
            except Exception:  # noqa: BLE001 - an unreadable model is not evidence
                continue
            if not count:
                continue
            entry = {"model": model_name, "rows": count,
                     "from_module": module, "custom": is_custom,
                     "looks_like_customers": signals["looks_like_customers"],
                     "looks_like_plumbing": signals["looks_like_plumbing"],
                     "partner_links": signals["partner_links"],
                     "email_fields": signals["email_fields"],
                     "recency_fields": signals["recency_fields"]}
            if is_custom:
                # The agent needs the field names to spot which one shows a
                # customer going quiet.
                entry["fields"] = sorted(
                    name for name, field in model._fields.items()
                    if field.store and not name.startswith("message_")
                    and name not in ("id", "create_uid", "write_uid",
                                     "create_date", "write_date", "__last_update")
                )[:40]
            found.append(entry)
            if len(found) >= limit:
                break
        return found

    @staticmethod
    def _summarise(evidence):
        lines = []
        company = evidence.get("company", {})
        lines.append(_("Company: %s (%s)", company.get("name") or "?",
                       company.get("country") or _("country not set")))
        lines.append(_("Languages: %(all)s — the owner reads %(owner)s",
                       all=", ".join(evidence.get("languages", [])) or "-",
                       owner=evidence.get("owner_language") or "?"))
        lines.append(_("Apps in use: %s", ", ".join(evidence.get("installed_apps", [])) or "-"))
        volumes = evidence.get("record_volumes", {})
        if volumes:
            lines.append(_("Data found:"))
            for model_name, info in sorted(
                volumes.items(), key=lambda kv: -kv[1]["count"]
            ):
                lines.append("  · %s — %s (%s)" % (info["label"], info["count"], model_name))
        models = evidence.get("data_models", [])
        customerish = [m for m in models if m.get("looks_like_customers")]
        if customerish:
            lines.append(_("Models that look like customer records — start here:"))
            for entry in customerish[:8]:
                lines.append("  · %s — %s rows%s, recency via %s" % (
                    entry["model"], entry["rows"],
                    _(" (custom)") if entry.get("custom") else "",
                    ", ".join(entry.get("recency_fields", [])[:3]) or "?"))
        other_custom = [m for m in models
                        if m.get("custom") and not m.get("looks_like_customers")]
        if other_custom:
            lines.append(_("Other custom models (often logs and plumbing): %s",
                           ", ".join(m["model"] for m in other_custom[:10])))
        capabilities = evidence.get("capabilities", [])
        usable = [c for c in capabilities if c["available"]]
        if usable:
            lines.append(_("What the manager can work with here:"))
            for entry in usable:
                rows = sum(m["rows"] for m in entry["models"])
                lines.append("  · %s — %s (%s records)" % (
                    entry["label"], entry["enables"], rows))
        missing = [c["label"] for c in capabilities if not c["available"]]
        if missing:
            lines.append(_("Not available here: %s", ", ".join(missing)))
        tempo = evidence.get("tempo", {})
        lines.append(_("Activity: %(m30)s messages in 30 days, %(users)s internal users, "
                       "%(new)s new contacts in 90 days.",
                       m30=tempo.get("messages_last_30_days", 0),
                       users=tempo.get("internal_users", 0),
                       new=tempo.get("contacts_created_last_90_days", 0)))
        return "\n".join(lines)

    def action_survey(self):
        """Collect the evidence. Works with no AI provider configured at all."""
        for profile in self:
            evidence = profile._collect_evidence()
            profile.write({
                "evidence_json": json.dumps(evidence, indent=2, ensure_ascii=False, default=str),
                "evidence_summary": profile._summarise(evidence),
                "surveyed_at": fields.Datetime.now(),
                "state": "surveyed" if profile.state == "new" else profile.state,
            })
            profile._compute_recommended_agents()
        return True

    # ------------------------------------------------------------------
    # Step 2 — hand the evidence to the discovery agent
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # One study at a time
    # ------------------------------------------------------------------
    def _discovery_agent(self):
        agent = self.env.ref(
            "era_ai_manager.agent_discovery", raise_if_not_found=False)
        if not agent:
            raise UserError(_("The discovery agent is missing."))
        return agent

    def _running_study(self):
        """The study currently in flight, if there is one."""
        return self._discovery_agent().sudo().session_ids.filtered(
            lambda session: session.state == "running")

    @staticmethod
    def _process_alive(pid):
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return False
        return True

    def action_stop_study(self):
        """Release a study that is marked running but is not.

        A session whose process died leaves the record stuck in 'running', and
        aidoo's reaper only sweeps it once the timeout has passed — so without
        this the owner is locked out of their own manager for a quarter of an
        hour with nothing to press.
        """
        self.ensure_one()
        running = self._running_study()
        if not running:
            raise UserError(_("No study is running."))
        running.action_stop()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Study stopped"),
                "message": _("You can start a new one now."),
                "sticky": False,
            },
        }

    def action_study_with_ai(self):
        """Run the discovery agent now, so the owner does not wait for a cron.

        The agent reads the evidence, explores further with its own tools, and
        writes business_summary, persona_brief and proposed_watchlists back
        onto this record. Nothing takes effect until Apply.
        """
        self.ensure_one()
        # Never launch a second study over a first. aidoo would silently defer
        # this one and we would still tell the owner it had started, which is
        # how a quiet failure becomes a lie.
        running = self._running_study()
        if running:
            session = running[0]
            if self._process_alive(session.last_run_pid):
                raise UserError(_(
                    "A study has been running since %s. Wait for it to finish "
                    "rather than starting another — two studies would fight "
                    "over the same record.",
                    fields.Datetime.to_string(session.write_date)))
            raise UserError(_(
                "A study is marked as running since %s but its process is "
                "gone, so it will never finish. Press 'Stop the study' and "
                "start again.",
                fields.Datetime.to_string(session.write_date)))
        if not self.evidence_json:
            self.action_survey()
        agent = self._discovery_agent()
        agent.action_run_now()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Studying the business"),
                "message": _(
                    "The discovery agent is reading your database. Refresh this "
                    "page in a minute to review the brief it proposes."),
                "sticky": False,
            },
        }

    def action_restudy(self):
        """Throw the current proposal away and study from scratch.

        The discovery agent is told to stop when a complete proposal already
        sits on the record and the survey has not moved — otherwise it would
        rewrite a perfectly good brief every month for nothing. The cost of
        that guard is that there was no way to say "do it again" when the
        brief itself is what is wrong: written before a fix, in the wrong
        language, or simply poor. Clearing the fields is that signal, and it
        is the only honest one, because the agent decides by looking at them.

        The evidence is re-surveyed at the same time, so the new study starts
        from the current state of the database rather than a stale snapshot.
        """
        self.ensure_one()
        self.write({
            "business_summary": False,
            "persona_brief": False,
            "proposed_watchlists": False,
            "apply_report": False,
            "state": "surveyed",
        })
        self.action_survey()
        return self.action_study_with_ai()

    # ------------------------------------------------------------------
    # Step 3 — put the brief into effect
    # ------------------------------------------------------------------
    def action_apply(self):
        """Write the reviewed brief onto the persona and create the watchlists.

        Deliberately a separate, explicit step: the AI proposes, a human
        decides. Applying is also idempotent, so a corrected brief can simply
        be applied again.
        """
        self.ensure_one()
        if not self.persona_brief:
            raise UserError(
                _("There is no brief to apply yet. Survey the business and let "
                  "the discovery agent draft one first."))
        persona = self.env.ref(
            "era_ai_manager.persona_manager", raise_if_not_found=False
        )
        if not persona:
            raise UserError(_("The manager persona is missing."))
        persona.sudo().write({"instructions": self.persona_brief})

        report = self._apply_watchlists()
        report["lines"].extend(self._apply_cadence())
        self._compute_recommended_agents()
        self.write({
            "state": "applied",
            "applied_at": fields.Datetime.now(),
            "apply_report": self._render_apply_report(report),
        })
        held = report["held"]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                # Anything held or rejected is news the owner has to act on, so
                # do not dress it up as a plain success.
                "type": "warning" if (held or report["rejected"]) else "success",
                "title": _("Brief applied"),
                "message": _(
                    "%(ok)s watchlist(s) approved, %(held)s held for your "
                    "approval, %(bad)s rejected. See 'What happened when you "
                    "applied'.",
                    ok=report["approved"], held=held, bad=report["rejected"]),
                "sticky": bool(held or report["rejected"]),
            },
        }

    @api.model
    def _action_link(self, xmlid, label):
        """A link to a menu/action, for "open the queue" style calls to action."""
        action = self.env.ref(xmlid, raise_if_not_found=False)
        if not action:
            return label
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return '<a href="%s/odoo/action-%s" target="_blank">%s</a>' % (
            base, action.id, label)

    @api.model
    def _record_link(self, record, label):
        """A real link to a record, so a count is something you can open.

        "2 matching now" is a fact the reader cannot act on; a link to the two
        of them is. /odoo/<model>/<id> is the form core itself uses.
        """
        base = self.env["ir.config_parameter"].sudo().get_param(
            "web.base.url", "")
        return '<a href="%s/odoo/%s/%s" target="_blank">%s</a>' % (
            base, record._name, record.id, label)

    def _apply_cadence(self):
        """Retime the campaign agent to the rhythm this business earns."""
        self.ensure_one()
        days = self.proposed_campaign_days or 30
        days = max(7, min(180, days))
        agent = self.env.ref(
            "era_ai_manager.agent_campaign", raise_if_not_found=False
        )
        if not agent:
            return []
        agent.sudo().write({"interval_number": days, "interval_type": "days"})
        return ["<li>%s</li>" % _(
            "I will propose one campaign every %s days — you approve it before "
            "anything is sent.", days)]

    def _compute_recommended_agents(self):
        """Which duties have anything to do on this database.

        Switching on an agent whose capability is absent produces a run that
        finds nothing, every time, for ever — noise that teaches the owner to
        ignore the run log.
        """
        self.ensure_one()
        capabilities = {c["capability"]: c
                        for c in (self._detect_capabilities() or [])}
        recommended = {"followup", "watchdog", "weekly"}
        if any(capabilities.get(key, {}).get("available")
               for key in ("support_desk", "leads")):
            recommended.add("inbox")
        if capabilities.get("email_campaigns", {}).get("available"):
            recommended.add("campaign")
        if not self.env["era.ai.watchlist"].search_count([("state", "=", "approved")]):
            recommended.discard("followup")
        self.recommended_agents = ", ".join(sorted(recommended))
        return recommended

    def _render_apply_report(self, report):
        """The report a person reads, not a status dump.

        "APPROVED  X — 2 matching now" tells the reader almost nothing: not
        two of what, not who they are, not whether anything is about to be
        sent. This says what will happen, to how many, and links to them.
        """
        if not report["lines"]:
            return "<p>%s</p>" % _(
                "There were no audiences to set up, so nothing changed.")

        outreach = self.env.ref(
            "era_ai_manager.action_era_ai_outreach", raise_if_not_found=False)
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        queue_link = (
            '<a href="%s/odoo/action-%s" target="_blank">%s</a>'
            % (base, outreach.id, _("Outreach"))) if outreach else _("Outreach")

        parts = ["<p>%s</p>" % _(
            "The manager now works from this brief. Here is what changed:")]
        parts.append("<ul>%s</ul>" % "".join(report["lines"]))

        if report["held"] or report["rejected"]:
            parts.append("<p><b>%s</b></p>" % _(
                "Some audiences are not running: %(held)s need your approval "
                "and %(bad)s could not be set up. Everything else is live.",
                held=report["held"], bad=report["rejected"]))

        parts.append("<p>%s</p>" % _(
            "Nothing reaches a customer without passing the queue first. "
            "Drafts wait for you in %s while you are in Ramp mode.", queue_link))
        return "".join(parts)

    def _apply_watchlists(self):
        """Create/update watchlists from the proposal and report on every one.

        One bad suggestion must not block the good ones, so each entry is
        validated on its own. Nothing is dropped silently: an audience the
        owner believes is covered but which never applied is worse than an
        obvious failure.
        """
        self.ensure_one()
        if not self.proposed_watchlists:
            return 0
        try:
            proposals = json.loads(self.proposed_watchlists)
        except (ValueError, TypeError) as error:
            raise UserError(
                _("The proposed watchlists are not valid JSON: %s", error)) from error
        if isinstance(proposals, dict):
            proposals = proposals.get("watchlists") or []
        if not isinstance(proposals, list):
            raise UserError(_("The proposed watchlists must be a list."))

        Watchlist = self.env["era.ai.watchlist"]
        Model = self.env["ir.model"].sudo()
        report = {"approved": 0, "held": 0, "rejected": 0, "lines": []}
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            model_name = proposal.get("model")
            label = proposal.get("name") or model_name or "?"
            model_record = Model.search([("model", "=", model_name)], limit=1)
            if not model_record or self.env.get(model_name) is None:
                report["rejected"] += 1
                report["lines"].append("<li>%s</li>" % _(
                    "I could not set up “%(name)s” because this database has no "
                    "%(model)s. Nothing was created for it.",
                    name=label, model=model_name))
                continue
            values = {
                "name": proposal.get("name") or model_name,
                "model_id": model_record.id,
                "domain": proposal.get("domain") or "[]",
                "partner_field": proposal.get("partner_field") or "partner_id",
                "play": proposal.get("play") or "check_in",
                "intent": proposal.get("intent") or proposal.get("name") or "",
                "priority": int(proposal.get("priority") or 50),
                "cooldown_days": int(proposal.get("cooldown_days") or 30),
            }
            existing = Watchlist.search([("name", "=", values["name"])], limit=1)
            try:
                # The savepoint is what makes "skip the bad one" true. Odoo
                # defers @api.constrains to flush time, so without forcing the
                # flush inside this block an invalid domain would sail past the
                # except clause and land in the database — and then either kill
                # the whole apply later or sit there matching nothing.
                with self.env.cr.savepoint():
                    if existing:
                        existing.write(values)
                    else:
                        Watchlist.create(values)
            except Exception as error:  # noqa: BLE001 - drop the bad one, keep the rest
                _logger.info("Rejected proposed watchlist %s: %s", label, error)
                report["rejected"] += 1
                report["lines"].append("<li>%s</li>" % _(
                    "I could not set up “%(name)s”: %(err)s Nothing was created "
                    "for it.", name=label, err=error))
                continue

            watchlist = existing or Watchlist.search(
                [("name", "=", values["name"])], limit=1)
            # Approving runs the blast-radius check, so a proposal that would
            # reach the entire customer base stays inert until a human says so.
            watchlist.action_approve()
            if watchlist.state == "approved":
                report["approved"] += 1
                count = watchlist.match_count
                # One link, not two to the same page: the watchlist form is
                # where both the people and the rule live.
                report["lines"].append("<li>%s</li>" % _(
                    "I am now watching %(who)s under “%(name)s” — %(link)s.",
                    who=_("%s customer(s)", count) if count else _("nobody yet"),
                    name=label,
                    link=self._record_link(
                        watchlist, _("open it to see them or change the rule")),
                ))
            else:
                report["held"] += 1
                report["lines"].append("<li>%s</li>" % _(
                    "I am NOT using “%(name)s” yet: %(why)s "
                    "Nobody will be contacted from it until you %(link)s and "
                    "approve it.",
                    name=label, why=watchlist.blocked_reason or "",
                    link=self._record_link(watchlist, _("open it")),
                ))
        return report

    # ------------------------------------------------------------------
    @api.model
    def current(self):
        """The profile this database works from, created on first use."""
        profile = self.search([("company_id", "=", self.env.company.id)], limit=1)
        if not profile:
            profile = self.create({"company_id": self.env.company.id})
        return profile

    @api.model
    def action_open_current(self):
        profile = self.current()
        return {
            "type": "ir.actions.act_window",
            "name": _("Business Profile"),
            "res_model": "era.ai.profile",
            "res_id": profile.id,
            "view_mode": "form",
            "target": "current",
        }

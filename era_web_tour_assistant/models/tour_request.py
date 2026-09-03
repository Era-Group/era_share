# -*- coding: utf-8 -*-
import logging
import re
import time
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import text_match
from . import tour_builder

_logger = logging.getLogger(__name__)

DEFAULT_MATCH_THRESHOLD = 0.5
MAX_QUESTION_LENGTH = 200

# A question that has defeated the agent this many times stops being retried;
# it stays in the queue for a person to record instead.
MAX_BUILD_ATTEMPTS = 3

# How long a claimed question may sit unfinished before it goes back in the
# queue. Claiming marks it "building" so two workers cannot take the same one;
# nothing marked it back, so a worker that died between claiming and answering
# — a crash, a lost connection, a container rebuilt under it — took the
# question with it for good. Generous, because a walkthrough legitimately
# takes minutes of a real browser and reclaiming one still being worked on
# would have two agents on it.
CLAIM_MINUTES = 45

# What a submitted step is allowed to ask the user to do. Anything else is
# refused: the steps arrive from a process outside Odoo, and a tour step is
# executed against the user's own session.
ALLOWED_RUNS = ("click", "edit", "hover", "press")
MAX_SUBMITTED_STEPS = 40
MAX_TRIGGER_LENGTH = 500


class TourAssistantRequest(models.Model):
    _name = "tour.assistant.request"
    _description = "Tour Assistant Request"
    _order = "ask_count desc, last_asked desc, id desc"

    name = fields.Char(
        string="Question",
        required=True,
        readonly=True,
        help="The question as it was first asked.",
    )
    question_key = fields.Char(
        string="Normalised Question",
        required=True,
        readonly=True,
        index=True,
        help="Folded form of the question. Two people asking the same thing "
             "differently land on the same record, so the count means demand.",
    )
    user_ids = fields.Many2many(
        "res.users",
        "tour_assistant_request_user_rel",
        "request_id",
        "user_id",
        string="Asked By",
        readonly=True,
    )
    ask_count = fields.Integer(
        string="Times Asked", default=0, readonly=True,
        help="How often this was asked. The queue is ordered by it, so the "
             "tour worth building next is at the top.",
    )
    last_asked = fields.Datetime(string="Last Asked", readonly=True)

    state = fields.Selection(
        [
            ("matched", "Answered by a Tour"),
            ("queued", "No Tour Yet"),
            ("building", "Being Built"),
            ("ready", "Tour Published"),
            ("dismissed", "Dismissed"),
        ],
        string="Status",
        default="queued",
        required=True,
        readonly=True,
        index=True,
    )
    tour_id = fields.Many2one(
        "web_tour.tour",
        string="Tour",
        ondelete="set null",
        readonly=True,
        help="The tour that answers this question.",
    )
    match_score = fields.Float(
        string="Match Score", readonly=True, digits=(3, 2),
        help="How much of the question the matched tour covered, 0 to 1.",
    )
    match_detail = fields.Char(
        string="Matched Words", readonly=True,
        help="Which words of the question reached the tour. Use it to work "
             "out what to add to the tour's keywords.",
    )
    build_attempts = fields.Integer(
        string="Build Attempts", default=0, readonly=True,
        help="How many times an agent has tried and failed to work this out. "
             "After a few it stops trying and leaves the question for a "
             "person to record.",
    )
    build_error = fields.Char(
        string="Why It Failed", readonly=True,
        help="What the agent reported on its last attempt.",
    )
    build_seconds = fields.Float(
        string="Seconds to Build", readonly=True, digits=(6, 1),
        help="How long the answer took to work out. Recorded so the wait a "
             "user is told to expect comes from what building actually costs "
             "here, rather than a number somebody guessed.",
    )
    step_count = fields.Integer(
        string="Steps", readonly=True,
        help="How many steps the walkthrough ended up with.",
    )
    tour_generated = fields.Boolean(
        string="Built Automatically",
        related="tour_id.assistant_generated",
        store=True,
        help="The answer was assembled from the menus rather than recorded by "
             "a person. Worth reviewing: it gets the user to the right "
             "screen, but it cannot explain the work itself.",
    )

    completed_count = fields.Integer(
        string="Times Completed", default=0, readonly=True,
        help="How often the matched tour was actually run to the end. A "
             "question asked far more often than its tour is completed is a "
             "sign the tour does not answer it.",
    )
    reported_count = fields.Integer(
        string="Times Reported", default=0, readonly=True,
        help="How often somebody running this walkthrough said it did not "
             "help. Views and menus differ from one database to the next, so "
             "the people using it are the only instrument that covers them "
             "all.",
    )
    completion_rate = fields.Float(
        string="Completion Rate",
        compute="_compute_completion_rate",
        store=True,
        aggregator="avg",
        help="Completions divided by times asked.",
    )

    _uniq_question_key = models.Constraint(
        "unique(question_key)",
        "Each question is tracked once; asking it again raises its count.",
    )

    # ------------------------------------------------------------------
    # Client entry points
    # ------------------------------------------------------------------

    @api.model
    def _match_threshold(self):
        """Score below which a tour is not considered an answer."""
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.match_threshold"
        )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_MATCH_THRESHOLD
        return value if 0.0 < value <= 1.0 else DEFAULT_MATCH_THRESHOLD

    @api.model
    def _answer_source(self):
        """Where answers are allowed to come from.

        ``agent`` — the default — means the agent is the only thing that
        writes a walkthrough. Tours a person recorded by hand and tours
        assembled from the menus are both ignored, however good they are:
        this module's promise is that the user asks and the system works it
        out, and half an answer from a menu tree undercuts that.

        Matching still runs, but only over what the agent itself built. That
        is the agent remembering its own work, not a second source competing
        with it — without it, the same question would pay for a fresh
        multi-minute run every single time it is asked.

        ``all`` restores the earlier behaviour for anyone who wants recorded
        tours and the menu builder back.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.answer_source", "agent"
        )
        return "all" if str(raw).strip().lower() == "all" else "agent"

    @api.model
    def _generation_enabled(self):
        """Whether unanswered questions get a walkthrough built for them.

        On by default. Turning it off leaves the queue as the only outcome,
        which is what an administrator who would rather record every tour by
        hand wants.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.generate_tours", "True"
        )
        return str(raw).strip().lower() not in ("0", "false", "no", "off")

    # How many builds must be on record before a wait is quoted. Two of them
    # can differ by a factor of three, and a number drawn from that is worse
    # than no number: a user told "about a second" who waits eight has been
    # misled, and will trust the next estimate less.
    MIN_TIMINGS = 8

    @api.model
    def build_estimate(self):
        """The wait to quote, in seconds, or 0 while it is not yet known.

        Read from what building has actually cost on this database rather than
        a constant, because it depends on how many menus the install carries
        and which provider the account points at — neither of which this module
        can know in advance.

        Returned as the middle and the ninetieth percentile, not the fastest
        and the slowest. Those are two different populations: a question the
        menus answer costs a tenth of a second, and one that needs the model
        costs several, so a range opening at the quickest reads as a promise
        the quick path will be taken. Whoever is asking cannot know which path
        their question will take, so what matters to them is the figure they
        will not often wait longer than.
        """
        rows = self.sudo().search_read(
            [("build_seconds", ">", 0)], ["build_seconds"],
            order="id desc", limit=50,
        )
        if len(rows) < self.MIN_TIMINGS:
            return 0.0, 0.0
        seconds = sorted(row["build_seconds"] for row in rows)
        middle = seconds[len(seconds) // 2]
        # Index clamped so a short sample cannot walk off the end.
        upper = seconds[min(len(seconds) - 1, int(len(seconds) * 0.9))]
        return round(middle, 1), round(upper, 1)

    @api.model
    def ask(self, question):
        """Answer a user's question, or record that nothing answers it.

        Returns a dict the web client acts on directly:

        - ``{"state": "matched", "tour": {"name", "url"}, ...}`` — start it
        - ``{"state": "queued", ...}`` — tell the user it has been noted
        """
        question = (question or "").strip()
        if not question:
            raise UserError(_("Please type what you are trying to do."))
        question = question[:MAX_QUESTION_LENGTH]

        key = " ".join(
            sorted({text_match.stem(word) for word in text_match.question_tokens(question)})
        )
        if not key:
            # Not an error. Typing "كيف" and typing "مساعدة" are the same act —
            # somebody who has not said what they want yet — and they were
            # answered two different ways: one with a red dialog that reads as
            # a fault of theirs, the other with a sentence in the box they are
            # already looking at. Found by asking three hundred questions, of
            # which exactly two produced a dialog.
            return {
                "state": "queued",
                "message": _(
                    "Tell me what you are trying to do — a few words about the "
                    "task, not only how or what."
                ),
            }

        # sudo: a request is a shared demand counter, so answering a question
        # someone else already asked has to update their record. Users are
        # given no write access to this model precisely so the count cannot be
        # edited by hand; only the fields below are touched.
        record = self.sudo().search([("question_key", "=", key)], limit=1)

        tour, score, matched = self.env["web_tour.tour"]._assistant_best_match(
            question
        )
        answered = bool(tour) and score >= self._match_threshold()

        if not answered and record.tour_id.assistant_enabled:
            # Answered before, by a tour whose wording drifted from this
            # phrasing. Reuse it rather than building a second one.
            if record.tour_id._assistant_is_visible_to_user():
                tour, answered = record.tour_id, True

        # Still nothing: assemble one from the menus and views this user can
        # reach. Off unless the answer source is widened, since a menu tree
        # can say where something is but never how the work is done.
        build_seconds = 0.0
        unavailable = ""
        if not answered and self._answer_source() == "all" \
                and self._generation_enabled():
            started = time.monotonic()
            try:
                built, unavailable = self.env[
                    "tour.assistant.builder"
                ].build_with_reason(question)
            except Exception:
                # Somebody typed a question and pressed a button. Whatever went
                # wrong working the answer out, a traceback is not an answer,
                # and the queue entry that follows is one — the question is
                # still recorded and still counts as demand.
                _logger.exception(
                    "Tour Assistant could not build for %r", question[:80]
                )
                built, unavailable = self.env["web_tour.tour"], ""
            # Timed whether or not it produced anything: a question that took
            # eight seconds to come back empty cost the user the same wait as
            # one that worked, and an estimate drawn only from the successes
            # would understate it.
            build_seconds = time.monotonic() - started
            if built:
                tour, answered = built, True
                score, matched = 0.0, []
        values = {
            "ask_count": (record.ask_count if record else 0) + 1,
            "last_asked": fields.Datetime.now(),
            "user_ids": [fields.Command.link(self.env.user.id)],
        }
        if build_seconds:
            values["build_seconds"] = round(build_seconds, 1)
        if unavailable:
            # Kept on the record so the queue shows an administrator why these
            # questions went unanswered — a column of "the app is not
            # installed" is a purchasing conversation, not a backlog.
            values["build_error"] = unavailable[:255]
        if answered:
            values.update({
                "state": "matched",
                "tour_id": tour.id,
                "match_score": score,
                "match_detail": ", ".join(matched),
                "step_count": len(tour.step_ids),
            })
        elif not record:
            values.update({
                "name": question,
                "question_key": key,
                "state": "queued",
                "match_score": score,
                "match_detail": ", ".join(matched),
            })

        if record:
            record.write(values)
        else:
            values.setdefault("name", question)
            values.setdefault("question_key", key)
            try:
                with self.env.cr.savepoint():
                    record = self.sudo().create(values)
            except IntegrityError:
                # Two people asked the same new question at once. The window is
                # not milliseconds: working the answer out sits between the
                # lookup and the write, so it is as wide as a build — six to
                # sixteen seconds here. And the question two people ask
                # together is exactly the popular one this is built for.
                record = self.sudo().search(
                    [("question_key", "=", key)], limit=1)
                if not record:
                    raise
                values["ask_count"] = record.ask_count + 1
                record.write(values)

        if answered:
            self.env.user._assistant_start_tour(tour)
            return {
                "state": "matched",
                "request_id": record.id,
                "tour": {"name": tour.name, "url": tour.url or "/odoo"},
            }
        if unavailable:
            # The planner read every menu this user can reach and told us why
            # none of them answers — most often that the app the question is
            # about is not installed here. Somebody who cannot find leave
            # because their Odoo has no HR is far better served by being told
            # so than by a queue message implying it is coming.
            message = unavailable
        elif self._worker_expected():
            # An agent is watching the queue, so this is a wait, not a refusal.
            message = _(
                "Nothing covers that yet, so one is being put together for "
                "you now. Ask again in a few minutes."
            )
        else:
            message = _(
                "No walkthrough covers that yet. Your question has been "
                "recorded — it has now been asked %(count)s time(s).",
                count=record.ask_count,
            )
        return {
            "state": record.state if record.state != "matched" else "queued",
            "request_id": record.id,
            "message": message,
        }

    @api.model
    def _worker_expected(self):
        """Whether an agent is watching the queue.

        The module cannot see the worker — it is a separate process — so this
        is declared. It only changes what the user is told: promising that a
        walkthrough is on its way when nothing is running would be worse than
        saying plainly that the question was written down.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.worker_running", "False"
        )
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    @api.depends("ask_count", "completed_count")
    def _compute_completion_rate(self):
        for record in self:
            record.completion_rate = (
                record.completed_count / record.ask_count if record.ask_count else 0.0
            )

    @api.model
    def _register_completion(self, tour):
        """Note that ``tour`` was run to the end by the current user.

        Called when the web client reports a finished tour. Only requests the
        user actually asked are touched, so completing a tour reached some
        other way does not inflate anyone's numbers.
        """
        # sudo: the counter is shared across everyone who asked the question,
        # and users have no write access to this model by design.
        # Most recently asked first: the tour that just finished is the one
        # the user asked for last, not the one they ask most often.
        record = self.sudo().search(
            [
                ("tour_id", "=", tour.id),
                ("user_ids", "in", self.env.user.id),
                ("state", "in", ("matched", "ready")),
            ],
            order="last_asked desc, id desc",
            limit=1,
        )
        if record:
            record.completed_count += 1
        return bool(record)

    # ------------------------------------------------------------------
    # Manager actions
    # ------------------------------------------------------------------

    def action_create_tour(self):
        """Start a draft tour for this question and open it for recording."""
        self.ensure_one()
        if self.tour_id:
            raise UserError(_("This question already has a tour."))
        tour = self.env["web_tour.tour"].create({
            "name": self._draft_tour_name(),
            "assistant_description": self.name,
            "assistant_enabled": False,
            "custom": True,
        })
        self.write({"tour_id": tour.id, "state": "building"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "web_tour.tour",
            "res_id": tour.id,
            "view_mode": "form",
            "target": "current",
        }

    def _draft_tour_name(self):
        """A unique technical name, since web_tour.tour enforces uniqueness."""
        self.ensure_one()
        base = "assistant_%s" % self.id
        existing = self.env["web_tour.tour"].search_count([("name", "=", base)])
        return base if not existing else "%s_%s" % (base, existing + 1)

    def action_publish(self):
        """Offer the linked tour to everyone who asks this from now on."""
        for record in self:
            if not record.tour_id:
                raise UserError(
                    _("Link or create a tour before publishing %s.")
                    % record.name
                )
            if not record.tour_id.step_ids:
                raise UserError(
                    _("The tour for %s has no steps yet.") % record.name
                )
            record.tour_id.assistant_enabled = True
            record.state = "ready"
        return True

    def action_dismiss(self):
        """Stop showing this question in the queue."""
        self.write({"state": "dismissed"})
        return True

    def action_requeue(self):
        self.write({"state": "queued"})
        return True

    # ------------------------------------------------------------------
    # The agent worker
    # ------------------------------------------------------------------
    #
    # A worker is a process outside Odoo that drives a real browser against a
    # scratch database, performs the task a question asks about, and reports
    # the steps back. It reaches these three methods over JSON-RPC as a
    # dedicated user. Nothing it sends is trusted: every step is validated
    # here before it can become a tour a real user will follow.

    @api.model
    def worker_claim(self, limit=1):
        """Hand the agent the questions most worth solving.

        Ordered by how often each was asked, so the agent spends its time
        where the staff actually are. Claiming marks them ``building`` so two
        workers cannot pick up the same question.
        """
        self._check_worker_access()
        records = self.sudo().search(
            [
                ("state", "=", "queued"),
                ("build_attempts", "<", MAX_BUILD_ATTEMPTS),
            ],
            order="ask_count desc, last_asked desc",
            limit=max(1, min(int(limit or 1), 10)),
        )
        records.write({"state": "building"})
        return [
            {
                "id": record.id,
                "question": record.name,
                "ask_count": record.ask_count,
                # The agent has to explore as somebody who can reach the same
                # screens, or it learns a route the asker cannot walk.
                "asker_groups": record.user_ids.all_group_ids.mapped(
                    "full_name"
                )[:50],
                "lang": record.user_ids[:1].lang or self.env.lang or "en_US",
            }
            for record in records
        ]

    @api.model
    def worker_submit(self, request_id, steps, url="/odoo", group_xmlids=None):
        """Turn steps the agent discovered into a published tour.

        Returns the tour's name, or raises if the steps are unusable — the
        worker is a separate process and a malformed submission must fail
        loudly rather than publish a tour that strands people.
        """
        self._check_worker_access()
        record = self.sudo().browse(int(request_id)).exists()
        if not record:
            raise UserError(_("No such question."))

        clean = self._validate_steps(steps)
        tour = self.env["web_tour.tour"].sudo().create({
            "name": self.env["tour.assistant.builder"]._unique_name(record.name),
            "url": self._validate_url(url),
            "custom": True,
            "assistant_enabled": True,
            "assistant_generated": True,
            # Stamped and menu-listed like anything else the module builds, or
            # the two gates that govern generated walkthroughs both close on
            # it: unstamped reads as older than every builder, and naming no
            # menus reads as menus deleted under it. The agent drives a real
            # browser for minutes to work one of these out, and it would have
            # been withheld from everybody and swept inside a day.
            "assistant_builder_version": tour_builder.BUILDER_VERSION,
            "assistant_menu_ids": [(6, 0, self._menus_in_steps(clean).ids)],
            "assistant_description": record.name,
            "assistant_group_ids": [(6, 0, self._resolve_groups(group_xmlids))],
            "step_ids": [
                (0, 0, dict(step, sequence=(index + 1) * 10))
                for index, step in enumerate(clean)
            ],
        })
        record.write({
            "tour_id": tour.id,
            "state": "ready",
            "build_error": False,
        })
        return {"tour": tour.name, "steps": len(clean)}

    @api.model
    def _release_abandoned_claims(self):
        """Put back questions a worker claimed and never answered.

        Run from the cron rather than at claim time so a queue that nobody is
        working is still repaired, and so the repair is visible in the log of a
        database where the worker keeps dying.
        """
        cutoff = fields.Datetime.now() - timedelta(minutes=CLAIM_MINUTES)
        abandoned = self.sudo().search([
            ("state", "=", "building"),
            ("write_date", "<", cutoff),
        ])
        if not abandoned:
            return 0
        abandoned.write({"state": "queued"})
        _logger.info(
            "Tour Assistant: %d question(s) claimed and never answered are "
            "back in the queue.", len(abandoned))
        return len(abandoned)

    @api.model
    def _menus_in_steps(self, steps):
        """The menus a submitted walkthrough clicks, read from its own steps.

        The agent sends triggers, not menus, so the list the visibility gate
        needs has to be recovered from them. Every menu entry the web client
        draws carries its external id, and that is what a menu step aims at —
        so the ids are already in the steps, and this only resolves them.

        A walkthrough whose menus cannot be recovered names none, and is then
        withheld. That is the safe direction: it crosses screens nobody
        checked the reader can reach.
        """
        found = self.env["ir.ui.menu"]
        for step in steps:
            for xmlid in re.findall(
                r'data-menu-xmlid="([^"]+)"', step.get("trigger") or ""
            ):
                menu = self.env.ref(xmlid, raise_if_not_found=False)
                if menu and menu._name == "ir.ui.menu":
                    found |= menu
        return found

    @api.model
    def worker_fail(self, request_id, message):
        """Record that the agent could not work this one out."""
        self._check_worker_access()
        record = self.sudo().browse(int(request_id)).exists()
        if not record:
            return False
        attempts = record.build_attempts + 1
        record.write({
            "build_attempts": attempts,
            "build_error": (message or "")[:500],
            # Back to the queue while attempts remain; after that it stays
            # queued but is no longer claimable, which is the signal that a
            # person needs to record this one.
            "state": "queued",
        })
        return True

    # ------------------------------------------------------------------
    # Guarding what the worker sends
    # ------------------------------------------------------------------

    @api.model
    def _check_worker_access(self):
        if not self.env.user.has_group(
            "era_web_tour_assistant.group_tour_assistant_worker"
        ):
            raise AccessError(
                _("Only the tour assistant worker may call this.")
            )

    @api.model
    def _validate_steps(self, steps):
        """Reject anything that would not make a usable, safe tour."""
        if not isinstance(steps, (list, tuple)) or not steps:
            raise UserError(_("The agent returned no steps."))
        if len(steps) > MAX_SUBMITTED_STEPS:
            raise UserError(
                _("A walkthrough of %s steps is too long to be useful.")
                % len(steps)
            )

        clean = []
        for step in steps:
            if not isinstance(step, dict):
                raise UserError(_("Each step must be an object."))
            trigger = (step.get("trigger") or "").strip()
            if not trigger:
                raise UserError(_("A step with no trigger points at nothing."))
            if len(trigger) > MAX_TRIGGER_LENGTH:
                raise UserError(_("A step's trigger is implausibly long."))

            run = (step.get("run") or "").strip()
            if run and run.split(" ")[0] not in ALLOWED_RUNS:
                raise UserError(
                    _("A step asks the user to %s, which is not allowed.")
                    % run.split(" ")[0]
                )
            clean.append({
                "trigger": trigger,
                "content": (step.get("content") or "")[:500] or False,
                "run": run or False,
            })
        return clean

    @api.model
    def _validate_url(self, url):
        """A tour may only start somewhere inside this Odoo."""
        url = (url or "/odoo").strip()
        if not url.startswith("/") or url.startswith("//"):
            raise UserError(_("A tour must start on a path within Odoo."))
        return url[:200]

    @api.model
    def _resolve_groups(self, group_xmlids):
        """Restrict the tour to the groups the agent says it needs."""
        ids = []
        for xmlid in group_xmlids or []:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group and group._name == "res.groups":
                ids.append(group.id)
        return ids

    @api.model
    def report_unhelpful(self, tour_name):
        """Somebody ran this walkthrough and it did not help them.

        The one measurement no amount of testing here can supply. Every fault
        found in a walkthrough so far came from the shape of a view — a field
        inside a table, one hidden on a condition — and views differ from one
        client's database to the next. A person who was just pointed at
        nothing knows something this module cannot work out.

        Deliberately not a form: a sentence typed in frustration is worth less
        than the fact itself, and asking for one is how nobody reports
        anything.
        """
        tour = self.env["web_tour.tour"].sudo().search(
            [("name", "=", tour_name)], limit=1)
        if not tour:
            return False
        head = tour.assistant_first_stage_id or tour
        request = self.sudo().search([("tour_id", "=", head.id)], limit=1)
        if not request:
            return False
        request.reported_count += 1
        return True

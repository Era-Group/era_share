# -*- coding: utf-8 -*-
from odoo import api, fields, models

from . import text_match
from . import tour_builder

# How much of what was asked a tour has to account for before it may answer.
# Agreement alone is a mean of two directions, so a short walkthrough that is
# covered completely rides over a question that is not — and answering half a
# question instantly is worse than taking a few seconds to work all of it out.
#
# Above three fifths rather than at it: "امر بيع مع اضافة عميل جديد" against a
# walkthrough for "اضافة عميل جديد" is three words of five, which is exactly
# 0.6 and has to fail. Two words of three is a rewording and has to pass.
DEFAULT_COVERAGE_FLOOR = 0.65


class WebTourTour(models.Model):
    _inherit = "web_tour.tour"

    assistant_enabled = fields.Boolean(
        string="Offered by the Assistant",
        default=False,
        help="Let the assistant start this tour when a question matches it. "
             "Tours that are still being written should stay off.",
    )
    assistant_description = fields.Char(
        string="Answers the Question",
        help="What this tour teaches, in the words a user would use to ask "
             "for it — for example 'how do I add a new contact?'.",
    )
    assistant_keywords = fields.Char(
        string="Also Matches",
        help="Extra words that should reach this tour, separated by spaces or "
             "commas. Use it for the wording staff actually use, including "
             "synonyms and the other language.",
    )
    assistant_generated = fields.Boolean(
        string="Built by the Assistant",
        default=False,
        readonly=True,
        copy=False,
        help="This tour was assembled from the menus and views rather than "
             "recorded by hand. It gets someone to the right screen; a "
             "recorded tour will usually explain the work better.",
    )
    assistant_group_ids = fields.Many2many(
        "res.groups",
        "web_tour_tour_assistant_group_rel",
        "tour_id",
        "group_id",
        string="Restricted to Groups",
        help="Leave empty to offer this tour to everyone. Otherwise only "
             "members of these groups are shown it.",
    )
    assistant_menu_ids = fields.Many2many(
        "ir.ui.menu",
        "web_tour_tour_assistant_menu_rel",
        "tour_id",
        "menu_id",
        string="Menus It Walks Through",
        readonly=True,
        copy=False,
        help="Every menu this walkthrough visits. It is only offered to "
             "someone who can reach all of them.",
    )
    assistant_next_stage_id = fields.Many2one(
        "web_tour.tour",
        string="Next Stage",
        ondelete="set null",
        index=True,
        copy=False,
        help="The stage that follows this one. A walkthrough is cut into "
             "stages so that a user who does not need one can be moved past "
             "it without abandoning the rest.",
    )
    assistant_builder_version = fields.Integer(
        string="Built By Version",
        copy=False,
        help="Which version of the builder wrote this walkthrough. One written "
             "by an older version is no longer offered, and is removed so the "
             "question is answered again by the current one.",
    )
    assistant_skip_model = fields.Char(
        string="Stage Creates",
        copy=False,
        help="The model this stage exists to create a record of. A user who "
             "opens an existing one instead has already done what the stage "
             "teaches, and is carried to the next one.",
    )
    assistant_first_stage_id = fields.Many2one(
        "web_tour.tour",
        string="First Stage",
        ondelete="cascade",
        index=True,
        copy=False,
        help="The stage this chain starts at — the tour the question was "
             "matched to, and the one a completion is recorded against.",
    )

    @api.model
    def get_current_tour(self):
        """Keep onboarding tours from interrupting a tour someone asked for.

        Odoo starts the next unconsumed onboarding tour on every page load.
        Since reaching an assistant tour's starting page means a redirect,
        that would fire in the middle of the walkthrough the user is actually
        waiting for and take the pointer away from them.
        """
        if self.env.user.assistant_pending_tour_id:
            return False
        return super().get_current_tour()

    @api.model
    def consume(self, tourName):
        """Record that an assistant question was answered end to end.

        The web client calls this the moment a tour finishes, which makes it
        the one honest signal available that a walkthrough actually carried
        the user through the task.

        It is also where a chain moves on. The client starts whatever this
        returns, with no redirect — so handing back the next stage is how a
        walkthrough spans several of them without the user seeing a seam.
        """
        tour = self.search([("name", "=", tourName)], limit=1)
        following = tour.assistant_next_stage_id
        if following and following._assistant_is_visible_to_user():
            super().consume(tourName)
            # Not finished: the onboarding preference stays suspended and the
            # bookkeeping passes to the stage now starting, so a redirect in
            # the middle of the chain still cannot let an onboarding tour in.
            self.env.user._assistant_continue_tour(tour, following)
            return following.sudo()._get_tour_json()

        result = super().consume(tourName)
        if tour:
            # A completion belongs to the walkthrough, not to its last stage:
            # the question was asked once and the request points at the head.
            self.env["tour.assistant.request"]._register_completion(
                tour.assistant_first_stage_id or tour)
            self.env.user._assistant_finish_tour(tour)
        return result

    @api.model
    def _assistant_drop_stale(self):
        """Remove walkthroughs an older builder wrote.

        Filtering them out of the candidates stops them being offered; this is
        what stops them accumulating. Run from the cron the module already has,
        so an upgrade needs no migration script and a database that upgrades
        while nobody is looking still ends up clean.
        """
        stale = self.sudo().search([
            ("assistant_generated", "=", True),
            ("assistant_builder_version", "<", tour_builder.BUILDER_VERSION),
        ])
        if not stale:
            return 0
        # The requests that pointed at them go back to the queue rather than
        # to a dangling link, so the next asker gets a walkthrough built now.
        self.env["tour.assistant.request"].sudo().search(
            [("tour_id", "in", stale.ids)]
        ).write({"tour_id": False, "state": "queued"})
        count = len(stale)
        stale.unlink()
        return count

    @api.model
    def _assistant_reopen_after_upgrade(self):
        """Give up questions another chance when the builder has moved on.

        A question the agent failed to build three times stops being handed
        out, which is right: something about it defeats the builder, and
        handing it round forever wastes a worker on it. But nothing ever gave
        it another chance, so a question that failed under one builder was dead
        on that database however many times the builder was fixed afterwards —
        and it was fixed eight times in the two days this was written.

        Which version this database last cleared is remembered, so the reopen
        happens once per upgrade rather than every night.
        """
        setting = self.env["ir.config_parameter"].sudo()
        key = "web_tour_assistant.reopened_for_version"
        try:
            cleared = int(setting.get_param(key, 0))
        except (TypeError, ValueError):
            cleared = 0
        if cleared >= tour_builder.BUILDER_VERSION:
            return 0

        stuck = self.env["tour.assistant.request"].sudo().search([
            ("state", "=", "queued"),
            ("build_attempts", ">", 0),
        ])
        stuck.write({"build_attempts": 0, "build_error": False})
        setting.set_param(key, tour_builder.BUILDER_VERSION)
        return len(stuck)

    @api.model
    def assistant_library(self, limit=0):
        """The walkthroughs this user may run, most asked for first.

        Somebody who wants a walkthrough again had to type the question again
        and wait for it to be matched, or go into Settings, which is not a
        place a warehouse clerk goes. A list under the box turns the assistant
        from something you ask into something you can look through.

        Each row is checked with ``_assistant_is_visible_to_user`` — the same
        gate that decides whether a question may be answered with it — so a
        walkthrough crossing Purchase and Accounting is simply absent for
        somebody who has one of the two, rather than being offered and then
        refusing halfway.

        All of them, not a first dozen. The cap was there when the list was
        new and short; with a hundred and fifty answered questions it was
        hiding the ones a person was most likely to be looking for, since the
        order is by how often each was asked and yesterday's task is rarely
        the most asked. The list scrolls, and the box above it filters, which
        is what makes the whole of it usable rather than merely present.
        """
        tours = self.sudo().search([
            ("assistant_enabled", "=", True),
            ("assistant_first_stage_id", "=", False),
            "|",
            ("assistant_generated", "=", False),
            ("assistant_builder_version", ">=", tour_builder.BUILDER_VERSION),
        ])
        reachable = self._assistant_reachable_menu_ids()
        offered = tours.filtered(
            lambda tour: tour._assistant_is_visible_to_user(reachable))
        if not offered:
            return []

        requests = self.env["tour.assistant.request"].sudo().search(
            [("tour_id", "in", offered.ids)])
        asked = {request.tour_id.id: request for request in requests}

        rows = []
        for tour in offered:
            request = asked.get(tour.id)
            rows.append({
                "name": tour.name,
                "url": tour.url,
                # The question somebody typed is their wording, and every
                # colleague would see it. A title an administrator wrote wins
                # where there is one.
                "label": tour.assistant_description or tour.name,
                "asked": request.ask_count if request else 0,
                "steps": sum(
                    len(stage.step_ids)
                    for stage in self._assistant_chain(tour)
                ),
                "stages": len(self._assistant_chain(tour)),
            })
        rows.sort(key=lambda row: (-row["asked"], row["label"]))
        return rows[:limit] if limit else rows

    @api.model
    def _assistant_chain(self, tour):
        """A walkthrough's stages, in order, starting at ``tour``."""
        stages, seen = [], set()
        node = tour
        while node and node.id not in seen:
            seen.add(node.id)
            stages.append(node)
            node = node.assistant_next_stage_id
        return stages

    @api.model
    def assistant_stage_info(self, tourName):
        """What the browser needs to know about the stage now running.

        Only ``assistant_skip_model`` so far. It is fetched rather than shipped
        with the tour because a chained stage arrives from ``consume`` without
        passing through anything of ours.
        """
        tour = self.search([("name", "=", tourName)], limit=1)
        if not tour or not tour.assistant_next_stage_id:
            # Nothing to be carried to.
            return {}
        return {"skip_model": tour.assistant_skip_model or ""}

    @api.model
    def assistant_begin(self, tourName):
        """Told that this user is starting this walkthrough now.

        Asking a question goes through ``ask``, which records the walkthrough
        against the user before handing it to the browser. Running one again
        from the list did not: it started the tour in the browser and told the
        server nothing. So when the first stage finished there was no record of
        a walkthrough in progress, nothing to move the mark to, and the second
        stage never arrived — the walkthrough stopped dead at the end of stage
        one and nothing anywhere said why.

        It also puts Odoo's onboarding out of the way for the length of the
        walkthrough, which the replay path was skipping as well.
        """
        tour = self.search([("name", "=", tourName)], limit=1)
        if not tour or not tour._assistant_is_visible_to_user():
            return False
        self.env.user._assistant_start_tour(tour)
        return tour.sudo()._get_tour_json()

    @api.model
    def assistant_pending_stage(self):
        """The stage this user is part-way through, when none is running.

        ``consume`` hands the next stage back to the client, and the client
        drops it: it starts what it is given with neither ``fromDB`` nor a url,
        and ``startTour`` returns without a word for any tour that is not in
        the javascript registry — which is every tour this module generates.
        Proved in a browser: the same call starts the tour with ``fromDB`` and
        does nothing without it.

        So the browser asks instead of being told. Only a later stage is
        offered: the first one is started by whoever asked the question, and
        answering with it would race them.
        """
        tour = self.env.user.assistant_pending_tour_id
        if not tour or not tour.assistant_first_stage_id:
            return False
        if not tour._assistant_is_visible_to_user():
            return False
        return tour.sudo()._get_tour_json()

    @api.model
    def assistant_skip_stage(self, tourName):
        """The user reached this stage's outcome by a route of their own.

        Deliberately not ``consume``: a stage nobody walked has not been
        completed, and counting it would inflate the one honest measure of
        whether a walkthrough helps — the share of askers who reach the end.
        """
        tour = self.search([("name", "=", tourName)], limit=1)
        following = tour.assistant_next_stage_id
        if not following or not following._assistant_is_visible_to_user():
            return False
        self.env.user._assistant_continue_tour(tour, following)
        return following.sudo()._get_tour_json()

    def _assistant_is_visible_to_user(self, reachable_menu_ids=None):
        """Whether the current user is allowed to be offered this tour.

        Two independent gates, because they answer different questions. The
        groups are what an administrator declared about a tour they wrote. The
        menus are what a generated walkthrough actually clicks, and every one
        of them has to be reachable: a walkthrough spanning several screens is
        only as usable as its least reachable stage, and ``assistant_group_ids``
        cannot say so — it is satisfied by *any* of its groups, so the union of
        two screens' groups would offer stock's half of a task to somebody who
        only has manufacturing.
        """
        self.ensure_one()
        if self.assistant_group_ids \
                and not (self.assistant_group_ids & self.env.user.all_group_ids):
            return False
        if not self.assistant_menu_ids:
            # A tour somebody recorded by hand may legitimately name no menus:
            # its author decided who it is for with the groups above. A
            # generated one always records the menus it walks, so an empty list
            # means they were deleted — which is what uninstalling the app they
            # belonged to does. Every step then aims at a menu that no longer
            # exists, while the gate that should withhold it has nothing left
            # to check and lets it through to everybody.
            return not self.assistant_generated

        if reachable_menu_ids is None:
            reachable_menu_ids = set(self._assistant_reachable_menu_ids())
        return set(self.assistant_menu_ids.ids) <= reachable_menu_ids

    @api.model
    def _assistant_reachable_menu_ids(self):
        """The ids of every menu the current user can actually click.

        Taken from ``load_menus`` — the payload the web client builds its
        navbar from — rather than from the menu tree. The two disagree, and
        where they disagree the tree is wrong about what is on screen:
        hr_timesheet.timesheet_menu_activity_user is a child of the Timesheets
        app with an action of its own and passes every check the tree can make,
        and the client does not put it in the navbar at all. A step aimed at it
        waited for an element that was never coming, in two walkthroughs.

        Falls back to the tree if the payload cannot be read, since a module
        that cannot list menus at all is worse than one that lists too many.
        """
        try:
            payload = self.env["ir.ui.menu"].load_menus(False)
        except Exception:  # pragma: no cover - a broken payload is not fatal
            menus = self.env["ir.ui.menu"].search([])
            return set(menus._filter_visible_menus().ids)
        return {key for key in payload if isinstance(key, int)}

    @api.model
    def _assistant_candidates(self):
        """Enabled tours the current user may be offered.

        Narrowed to the agent's own work unless the answer source is widened
        — see ``tour.assistant.request._answer_source``.
        """
        domain = [("assistant_enabled", "=", True)]
        if self.env["tour.assistant.request"]._answer_source() == "agent":
            domain.append(("assistant_generated", "=", True))
        # A walkthrough written by an older builder is not offered. Every fix
        # to how steps are written is otherwise invisible to the questions
        # already answered on a database, which is how a tester was handed the
        # exact fault that had just been repaired, three times in one day.
        domain += [
            "|",
            ("assistant_generated", "=", False),
            ("assistant_builder_version", ">=", tour_builder.BUILDER_VERSION),
        ]
        tours = self.search(domain)
        # Worked out once rather than per tour: it walks the whole menu tree,
        # and a database with a few hundred generated walkthroughs would
        # otherwise pay for that on every question anybody asks.
        reachable = None
        if any(tour.assistant_menu_ids for tour in tours):
            reachable = self._assistant_reachable_menu_ids()
        return tours.filtered(
            lambda tour: tour._assistant_is_visible_to_user(reachable)
        )

    @api.model
    def _assistant_best_match(self, question):
        """Best answer to ``question``, as ``(tour, score, matched_words)``.

        An empty recordset and a score of 0.0 mean nothing came close enough
        to be worth starting; the caller decides what to do about that.
        """
        best = self.browse()
        best_score = 0.0
        best_matched = []
        asked = text_match.question_tokens(question)
        floor = self._assistant_coverage_floor()
        for tour in self._assistant_candidates():
            subject = " ".join(
                part for part in (tour.name, tour.assistant_description) if part
            )
            result, matched = text_match.score(
                question, subject, tour.assistant_keywords or ""
            )
            if asked and len(matched) / len(asked) < floor:
                # Agreement is a mean of two directions, and a short tour that
                # is covered completely can carry a question that is not.
                # "امر بيع مع اضافة عميل جديد" agreed 0.75 with a walkthrough
                # for "اضافة عميل جديد" and answered instantly, dropping the
                # sale order half of the question without a word. A confident
                # answer to half a question is worse than working the whole
                # one out, which is what happens when nothing matches.
                continue
            if result > best_score:
                best, best_score, best_matched = tour, result, matched
        return best, best_score, best_matched

    @api.model
    def _assistant_coverage_floor(self):
        """How much of the question a tour has to account for to answer it."""
        value = self.env["ir.config_parameter"].sudo().get_param(
            "web_tour_assistant.match_coverage", DEFAULT_COVERAGE_FLOOR)
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return DEFAULT_COVERAGE_FLOOR

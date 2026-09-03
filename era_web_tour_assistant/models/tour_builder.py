# -*- coding: utf-8 -*-
"""Building a walkthrough for a question nobody has recorded a tour for.

The steps are not invented. Every one of them is derived from something the
database already holds — a menu the asking user can see, the action behind it,
the fields its form view actually shows — so a generated trigger points at
something that exists by construction rather than by luck.

Nothing here opens a record or writes business data: it reads metadata and
emits a tour. That is what makes it safe to run against the live database on
demand, which is the whole point of answering the question while the user is
still looking at the screen.
"""

import ast
import hashlib

from lxml import etree

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.tools.safe_eval import (
    datetime as safe_eval_datetime,
    safe_eval,
    time as safe_eval_time,
)

from . import text_match

# Raised whenever a change alters the steps this module would write for a
# question it has already answered. A stored walkthrough carries the number it
# was built under, and one built under an older number is dropped rather than
# replayed — a fix that never reaches the walkthroughs already on a database is
# not a fix, and three times in one day a tester was handed exactly the fault
# that had just been repaired.
BUILDER_VERSION = 7

# A walkthrough longer than this stops being a walkthrough. Raised from 14
# once a walkthrough could span several screens: three stages of navigation,
# creation and required fields spend that much on their own, and truncating
# mid-task drops exactly the stages the user did not already know.
MAX_STEPS = 34

# Stricter than the matching threshold; see _build_threshold().
DEFAULT_BUILD_THRESHOLD = 0.6

# Two apps within this of each other means the question was ambiguous.
AMBIGUITY_MARGIN = 0.05

# Words that name what a record holds rather than which screen holds it.
# "The company's details" is a question about the company; the "details" part
# describes contents and matches nothing, while still counting against the
# question in the score. Contrast "bank statement", where "statement" is the
# subject itself — that one must keep its weight, which is why this list stays
# short and specific rather than becoming a general noun filter.
_GENERIC_WORDS = {
    "بيانات", "معلومات", "تفاصيل", "خصائص", "محتوي", "محتويات",
    "data", "details", "detail", "info", "information", "properties",
}

# The method names Odoo gives the button that moves a record to its next
# state. Measured rather than guessed at: on a fresh purchase order the first
# button the header shows is "Send RFQ", which opens a mail composer and takes
# the reader somewhere the walkthrough cannot follow. Confirming is the click
# that finishes what the stage started, and across purchase, sale, stock,
# account and manufacturing it is always one of these verbs.
_FORWARD_ACTIONS = ("confirm", "validate", "post", "approve", "process", "done")

# Widgets that draw something other than a box to fill. A status bar is the
# one that matters: account.payment marks state required, so "fill in State"
# became a step pointing at the status bar — which cannot be typed into, and
# which changes the record when it is clicked rather than accepting a value.
_DECORATIVE_WIDGETS = ("statusbar", "priority", "handle", "boolean_favorite",
                       "boolean_toggle")

# Words that mean the user wants to create something, not just find it.
_CREATE_WORDS = {
    "انشاء", "انشئ", "اضافه", "اضف", "اضيف", "جديد", "جديده", "تسجيل", "سجل",
    "ادخال", "ادخل", "اسوي", "اعمل", "سوي", "عمل", "اكتب", "كتابه",
    "create", "add", "new", "make", "record", "register", "enter", "write",
}


class TourBuilder(models.AbstractModel):
    _name = "tour.assistant.builder"
    _description = "Tour Assistant Builder"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @api.model
    def build(self, question):
        """A tour answering ``question``, or an empty recordset."""
        return self.build_with_reason(question)[0]

    @api.model
    def build_with_reason(self, question):
        """A tour answering ``question``, and why there is none.

        Runs as the asking user on purpose: the menus considered, the action
        opened and the fields read are all the ones that user is allowed to
        reach, so a generated tour can never walk somebody into a page they
        would be refused.
        """
        spoken = self.env.context.get("lang") or self.env.user.lang
        if spoken and spoken != self.env.context.get("lang"):
            # A walkthrough is text, stored once and read by a person later.
            # Which language it is stored in has to come from that person, not
            # from whatever context the build happened to run under: 205 of the
            # 210 walkthroughs on this database were written in English for
            # Arabic-speaking users, because the scripts that built them ran
            # with no language in context and every _() fell back to the
            # source. Nothing about that was visible until somebody read one.
            return self.with_context(lang=spoken).build_with_reason(question)

        stages, reason = self._plan(question)
        if not stages:
            return self.env["web_tour.tour"], reason

        built, visited, total = [], self.env["ir.ui.menu"], 0
        for stage in stages:
            made = self._stage_steps(
                stage, opening=not built,
                after_create=any(entry[2] for entry in built))
            if not made:
                # A stage whose menu has no external id cannot be pointed at.
                # Dropping it and keeping the rest beats abandoning a plan that
                # was mostly sound, and the stages are independent screens.
                continue
            if built and total + len(made) > MAX_STEPS:
                # Stop on a whole stage rather than in the middle of one. Cut
                # anywhere else and the walkthrough ends between New and Save,
                # leaving somebody in a half filled form with the pointer gone
                # — worse than ending early, because it looks like a fault
                # rather than the end. The first stage is taken whatever its
                # length, since half of one screen still beats nothing.
                break
            built.append((stage["menu"], made[:MAX_STEPS], self._stage_creates(stage)))
            visited |= stage["menu"]
            total += len(made)

        if not built:
            return self.env["web_tour.tour"], ""
        return self._create_chain(question, visited, built), ""

    @api.model
    def _plan(self, question):
        """The ordered stages that answer ``question``, and why there are none.

        Word overlap answers the questions that name their screen outright, and
        answers them without spending a model call or a second of the user's
        time. Everything else is a language problem or a task problem, and both
        are the planner's.
        """
        planner = self.env["tour.assistant.planner"]
        menu, score = self._best_menu(question)

        if menu and score >= self._trust_threshold():
            # The question and the menu say the same thing. There is nothing
            # for a model to add, and asking one would only make every clear
            # question slower.
            return [self._plain_stage(menu, question)], ""

        # Agreement this partial is where the confident wrong answers come
        # from: "طلب اجازة" and "طلبات الصيانة" share the word طلب and nothing
        # else, which was enough to walk somebody asking about leave into a
        # maintenance form. Sharing some words is evidence, not a decision.
        stages, reason = planner.plan_task(question, self._candidate_menus(question))
        if stages:
            return stages, ""
        if menu and not planner.available():
            # Nothing could be asked, so a partial match is all there is. It
            # was good enough before there was a planner at all.
            return [self._plain_stage(menu, question)], ""
        # Either the planner read the list and would not pick anything — which
        # is an answer worth passing on — or there was no match to begin with.
        return [], self._name_the_obstacle(question, reason)

    @api.model
    def _name_the_obstacle(self, question, reason):
        """Say whether the screen is absent or merely out of this user's reach.

        The planner only ever sees the menus the asking user can click, so
        every refusal it writes says some version of "the app for that is not
        installed". Measured with a plain employee, a stock keeper and an
        accountant: the Sales app was installed the whole time and all three
        were told it was not.

        That misleads twice. The employee believes the system cannot do it, and
        the queue's column of "not installed" — which this module presents to
        an owner as a purchasing conversation — fills with things they already
        own and cannot see. Whether anybody at all can reach that screen is one
        more query, and it changes the answer entirely.
        """
        if not reason:
            return reason
        # Not _best_menu: that refuses to choose when several apps answer a
        # question equally, which is right for building and wrong here — "امر
        # بيع" reaches Sales, Purchase and more, so it returned nothing and
        # this concluded nobody could reach it. The question is only whether
        # some screen out there agrees, not which one.
        #
        # with_user rather than sudo: sudo lifts access checks but leaves the
        # user's identity in place, and load_menus is keyed on identity, so
        # "as somebody else" read the asking user's own menus.
        # The asking user's language travels with the identity change, or the
        # menus come back in English, an Arabic question agrees with none of
        # them, and every refusal reads as "nobody can reach this" — which is
        # the answer this exists to avoid giving.
        elsewhere = self.with_user(SUPERUSER_ID).with_context(
            lang=self.env.lang)._best_agreement(question)
        if elsewhere < self._build_threshold():
            return reason
        return _(
            "%(reason)s\nThe screen for this does exist in your Odoo — it is "
            "your access that does not reach it. Ask whoever administers it "
            "for the permission rather than for the app.",
            reason=reason,
        )

    @api.model
    def _plain_stage(self, menu, question):
        """A stage nobody explained: one screen, and the question's own intent."""
        return {
            "menu": menu,
            "goal": "",
            "create": self._wants_to_create(question),
            "fields": [],
        }

    # ------------------------------------------------------------------
    # Finding what the question is about
    # ------------------------------------------------------------------

    @api.model
    def _trust_threshold(self):
        """Agreement at or above which a menu is taken without a second look.

        Deliberately high. Below it the question and the menu share some words
        and differ on others, which is exactly the shape of a coincidence —
        and a coincidence acted on confidently is the failure this module
        cares about most. Above it they say the same thing, and spending a
        model call to confirm what is already unambiguous only makes every
        clear question slower.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.trust_threshold", "0.8"
        )
        try:
            return min(max(float(raw), 0.0), 1.0)
        except (TypeError, ValueError):
            return 0.8

    @api.model
    def _candidate_menus(self, question):
        """The menus worth offering the planner, best agreement first.

        Ordered rather than filtered. What reaches here is either a tie the
        matcher would not break — where the right answer is near the top — or a
        question sharing no word with any menu, where every score is zero and
        the order carries nothing. So the list is sorted to keep whatever
        signal exists at the front of it, and the planner's own cap decides how
        much of the tail to spend.
        """
        menus = self.env["ir.ui.menu"].search([("action", "!=", False)])
        menus = self._clickable_menus(menus)
        scored = sorted(
            (
                (
                    text_match.balanced(
                        question, menu.name,
                        ignore=_CREATE_WORDS | _GENERIC_WORDS,
                    )[0],
                    menu.id,
                )
                for menu in menus
            ),
            key=lambda row: (-row[0], row[1]),
        )
        return self.env["ir.ui.menu"].browse([menu_id for dummy, menu_id in scored])

    @api.model
    def _build_threshold(self):
        """Agreement below which no tour is built.

        Deliberately stricter than the threshold for starting a tour someone
        wrote: that one was aimed at its question by a person, this one is a
        guess. A one-word menu name agrees completely with any question that
        happens to contain that word, so "reconcile a bank statement" reaches
        a menu called "Banks" — and walking someone into the wrong screen
        confidently is worse than telling them nothing was found.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.build_threshold"
        )
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_BUILD_THRESHOLD
        return value if 0.0 < value <= 1.0 else DEFAULT_BUILD_THRESHOLD

    @api.model
    def _best_menu(self, question):
        """The menu the question is asking about.

        Scored on the menu's own name rather than its full path: a user says
        "where do I find Users", not "where do I find Settings Users and
        Companies Users", so measuring against the whole path would punish
        every deeply buried menu. Matching an ancestor as well breaks ties
        between menus that share a name.
        """
        menus = self.env["ir.ui.menu"].search([("action", "!=", False)])
        menus = self._clickable_menus(menus)
        threshold = self._build_threshold()

        # Menu names are translated, and in a bilingual office people type
        # the term they know rather than the one the interface is showing.
        # One extra read covers both.
        english = {menu.id: menu.name for menu in menus.with_context(lang="en_US")}

        scored = []
        for menu in menus:
            covered, matched = text_match.balanced(
                question, menu.name, ignore=_CREATE_WORDS | _GENERIC_WORDS
            )
            other, other_matched = text_match.balanced(
                question, english[menu.id], ignore=_CREATE_WORDS | _GENERIC_WORDS
            )
            if other > covered:
                covered, matched = other, other_matched
            # One short word in common is a coincidence, not a match.
            if not any(len(word) >= 3 for word in matched):
                continue
            # Naming the path as well as the leaf is corroboration, and has to
            # count before the threshold rather than only when breaking a tie.
            # Only words the leaf did not already match count, though: Sales /
            # Orders / Orders would otherwise be rewarded twice for the single
            # word "order" and beat the Thobe Management order menu, which is
            # the one the question was actually about.
            parent = menu.parent_id.complete_name or ""
            if parent:
                also = set(text_match.balanced(question, parent)[1]) - set(matched)
                if also:
                    covered += 0.1
            if covered < threshold:
                continue
            scored.append((round(covered, 4), len(matched), menu))

        if not scored:
            return self.env["ir.ui.menu"], 0.0

        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        best = scored[0]

        # Two apps answering equally well means the question did not say which
        # one it meant. "A new thobe order" reaches Sales/Orders and Thobe
        # Management/Orders with the same agreement, and picking either one
        # confidently is how a user ends up on the wrong screen trusting it.
        # An honest queue entry beats a confident wrong turn.
        best_app = self._root_menu(best[2])
        for covered, dummy, menu in scored[1:]:
            if covered < best[0] - AMBIGUITY_MARGIN:
                break
            if self._root_menu(menu) != best_app:
                return self.env["ir.ui.menu"], 0.0

        return best[2], min(best[0], 1.0)

    @api.model
    def _root_menu(self, menu):
        node = menu
        while node.parent_id:
            node = node.parent_id
        return node

    @api.model
    def _menu_path(self, menu):
        """``menu`` and its ancestors, root first, or [] if any lacks an id.

        The web client marks each menu entry with its external id, so that is
        what a step has to aim at. A menu created by hand in the interface has
        none, and there is no other stable handle on it.
        """
        chain = []
        node = menu
        while node:
            chain.insert(0, node)
            node = node.parent_id

        xmlids = self.env["ir.ui.menu"].browse(
            [node.id for node in chain]
        )._get_menuitems_xmlids()
        if not all(xmlids.get(node.id) for node in chain):
            return []
        return [(node, xmlids[node.id]) for node in chain]

    @api.model
    def _wants_to_create(self, question):
        asked = text_match.question_tokens(question)
        return bool({text_match.stem(word) for word in asked} & {
            text_match.stem(word) for word in _CREATE_WORDS
        })

    # ------------------------------------------------------------------
    # Turning that into steps
    # ------------------------------------------------------------------

    @api.model
    def _best_agreement(self, question):
        """The closest any menu on screen comes to the question, 0 to 1.

        No tie-breaking and no ambiguity veto: this answers "does anything here
        match at all", which is a different question from "which one".
        """
        menus = self._clickable_menus(
            self.env["ir.ui.menu"].search([("action", "!=", False)]))
        # Both names, for the reason _best_menu reads both: in a bilingual
        # office people type the term they know rather than the one the
        # interface is showing.
        english = {menu.id: menu.name for menu in menus.with_context(lang="en_US")}
        best = 0.0
        for menu in menus:
            for name in (menu.name, english.get(menu.id)):
                if not name:
                    continue
                covered, matched = text_match.balanced(
                    question, name, ignore=_CREATE_WORDS | _GENERIC_WORDS)
                if covered > best and any(len(word) >= 3 for word in matched):
                    best = covered
        return best

    @api.model
    def _clickable_menus(self, menus):
        """Of ``menus``, the ones the web client actually draws.

        ``_filter_visible_menus`` answers "may this user see it", which is not
        the same question. hr_timesheet.timesheet_menu_activity_user is visible
        to everybody, has an action of its own, is not a section — and the
        client leaves it out of the navbar, so a step aimed at it waits for an
        element that never arrives. The payload the navbar is built from is the
        only honest answer, and it is what the visibility gate uses too.
        """
        drawn = self.env["web_tour.tour"]._assistant_reachable_menu_ids()
        return menus.filtered(lambda menu: menu.id in drawn)

    @api.model
    def _clickable_path(self, path):
        """The entries of ``path`` the navbar renders as something to click.

        Odoo's navbar draws a section that has children as a plain label and a
        section that has none as a list item with a click handler — see
        ``web.SectionMenu``. Both carry ``data-menu-xmlid``, so both look
        identical to a step written from the menu tree, and a walkthrough that
        named every level asked the user to click a heading. It resolved, it
        was never clickable, and the tour stopped there.

        What is left is the app, the section that opens its dropdown, and the
        entry the question was about. The headings between them describe where
        the entry sits; they are not part of getting to it.
        """
        if len(path) <= 2:
            return path
        return [path[0], path[1], path[-1]]

    @api.model
    def _stage_steps(self, stage, opening, after_create=False):
        """One screen of a walkthrough: get there, and do the work there.

        Returns nothing when the menu cannot be pointed at, which the caller
        treats as a stage to skip rather than a plan to abandon.

        ``after_create`` says some earlier stage in this chain made a record —
        any of them, not only the one before, because a walkthrough is one task
        and the bill at the end belongs to the order at the start. It is
        what allows a stage that only visits a screen to go further than the
        screen: without it, "review the order and confirm it" is a sentence
        with no step behind it, and the walkthrough turns into a sign that
        points at a room and stops talking.
        """
        menu = stage["menu"]
        path = self._menu_path(menu)
        if not path:
            # A menu with no external id cannot be pointed at reliably, and a
            # step that misses is worse than no step at all.
            return []

        steps = self._navigation_steps(path, opening=opening)
        if stage.get("goal"):
            # On the click that lands on the screen rather than the one that
            # leaves the last: the user reads it standing where the work is
            # about to happen, which is where knowing why they are here helps.
            steps[-1]["content"] = stage["goal"]
        if stage.get("create"):
            made = self._creation_steps(menu, named=stage.get("fields") or ())
            steps += made
            # Only where a record was actually made. A screen with no New
            # button — an action opening straight into a form, a menu behind a
            # server action — produces no creation steps at all, and a header
            # button on a page with no record open is a pointer aimed at
            # nothing. My own doing, found by reading rather than walking,
            # which is the only reason it is not in a database somewhere.
            forward = self._primary_button(menu) if made else None
            if forward:
                # Saving a request for quotation is not ordering from a
                # supplier, and saving a bill is not posting it. Without this
                # the walkthrough ends one click short of the task it promised.
                steps.append(forward)
        elif after_create:
            steps += self._handling_steps(menu)
        return steps

    @api.model
    def _handling_steps(self, menu):
        """Open the record this screen is about.

        A stage that is not a creation was navigation and nothing else: it
        arrived, said "review the order and confirm it", and left the user
        looking at a list with no pointer on anything. Measured on the purchase
        walkthrough — stage one had seven steps and stage two had four, all of
        them menu clicks.

        The step is only offered when the list has something in it, counted now
        and as the asking user, so it can never wait on a row that is not
        coming. That was the failure this module spent a day removing, and a
        count is the one way to rule it out rather than hope.
        """
        model_name = self._menu_model(menu)
        if not model_name:
            return []
        try:
            # As the asking user, and through the screen's own filter: half
            # these menus show a slice rather than a model. Inventory has
            # pickings and no receipts among them, and counting the model
            # would have promised a row the Receipts list does not have.
            if not self.env[model_name].search_count(
                    self._screen_domain(menu), limit=1):
                return []
        except Exception:
            return []
        return [{
            # Whichever record they came for. Naming it "the one you just
            # made" would be a guess — list order is the model's, not ours.
            "trigger": ".o_list_view .o_data_row:first-child,"
                       " .o_kanban_renderer .o_kanban_record",
            "content": _("Open the record you need from the list."),
            "run": "click",
        }]

    @api.model
    def _naming_field(self, model, action):
        """The field a record is named by, when the form draws it.

        The last resort of a creation stage, and it needs a looser rule than
        the rest: a vendor's name is drawn twice, once for a company and once
        for a person, each hidden by the other's condition, so "visible under
        every condition" rejects both and leaves the stage with no field at
        all. The condition is read instead, the same way a header button's is,
        and the trigger names the field rather than one of the two nodes — the
        tour engine takes the first visible match, which is whichever of them
        the form actually drew.
        """
        rec_name = model._rec_name or "name"
        if rec_name not in model._fields:
            return []
        try:
            arch = etree.fromstring(model.get_view(
                view_id=self._view_id(action, "form"), view_type="form")["arch"])
        except Exception:
            return []
        for node in arch.iter("field"):
            if node.get("name") != rec_name:
                continue
            if not self._fillable_here(node, model, action):
                continue
            described = model.fields_get([rec_name], ["string"])
            return [(rec_name, described.get(rec_name, {}).get("string") or rec_name)]
        return []

    @api.model
    def _first_fillable_field(self, model, action):
        """Anything at all the form opens with an empty box for.

        After the naming field, because some forms have none worth pointing at:
        a vendor bill names itself by a sequence, so the stage came out as New
        followed by Save with nothing between them — the same emptiness the
        naming field was added to fix, one model further along. What a bill
        actually needs first is its vendor, and the form says so by drawing it.

        One field, not all of them. This is the floor under a stage, not a
        replacement for the fields the planner names or the form requires.
        """
        try:
            arch = etree.fromstring(model.get_view(
                view_id=self._view_id(action, "form"), view_type="form")["arch"])
        except Exception:
            return []
        for node in arch.iter("field"):
            name = node.get("name")
            field = model._fields.get(name) if name else None
            if field is None or not field.store or field.readonly:
                continue
            if field.type in ("one2many", "many2many", "binary", "html"):
                # A table or an image is not a box, and neither is a field the
                # walkthrough cannot say anything useful about.
                continue
            if node.get("widget") in _DECORATIVE_WIDGETS:
                # A star or a drag handle: on the screen, editable, and no part
                # of the task. A purchase order draws its priority first, and
                # "fill in Priority" is not the help anybody asked for.
                continue
            if not self._fillable_here(node, model, action):
                continue
            described = model.fields_get([name], ["string"])
            return [(name, described.get(name, {}).get("string") or name)]
        return []

    @api.model
    def _fillable_here(self, node, model, action):
        """Whether this node is on the page and open to being typed in.

        The node's own condition is not the whole story. An invoice draws its
        number twice — once conditionally, once inside a container the form
        hides — and reading only the second said "fill in Number" on a screen
        with no such box, which a browser found on the first walkthrough it
        walked. Every ancestor's condition counts, and a field the form marks
        read-only is not something anybody fills.
        """
        if node.get("readonly") in ("1", "True", "true"):
            return False
        if any(parent.tag == "field" for parent in node.iterancestors()):
            # A column of an embedded table, not a field of this form — the
            # rule _on_the_open_page already states, and the reason an invoice
            # offered its number: the second of its two nodes is a column of
            # the lines below.
            return False
        for element in [node] + list(node.iterancestors()):
            for attribute in ("invisible", "column_invisible"):
                condition = (element.get(attribute) or "").strip()
                if condition and not self._shown_on_a_saved_record(
                        condition, model, action):
                    return False
        page = next((parent for parent in node.iterancestors()
                     if parent.tag == "page"), None)
        if page is not None:
            # Behind a tab nobody has opened yet, which is the knowledge the
            # walkthrough exists to supply rather than to assume.
            notebook = next((parent for parent in page.iterancestors()
                             if parent.tag == "notebook"), None)
            if notebook is not None and list(notebook)[0] is not page:
                return False
        return True

    @api.model
    def _screen_domain(self, menu):
        """The filter the screen behind this menu applies before it draws.

        Only what the action states outright. A server action computes its
        domain in code this cannot read, and reading nothing there is honest:
        the count is then over the whole model, which is the same guarantee
        this had before, no weaker.
        """
        action = menu.sudo().action
        expression = getattr(action, "domain", "") or ""
        if not expression or not isinstance(expression, str):
            return []
        try:
            domain = safe_eval(expression, {
                "uid": self.env.uid,
                "user": self.env.user,
                "allowed_company_ids": self.env.companies.ids,
                "context_today": lambda: fields.Date.context_today(self),
                "datetime": safe_eval_datetime,
                "time": safe_eval_time,
            })
            return domain if isinstance(domain, list) else []
        except Exception:
            # A domain resting on a context key nobody set here cannot be
            # honoured, and guessing one would be worse than counting wide.
            return []

    @api.model
    def _menu_model(self, menu):
        """Which model a menu's screen is about.

        Half the menus that matter open a server action rather than a window —
        Inventory's Receipts is one — and reading ``res_model`` off those
        returns nothing, which is why a stage on them stopped at navigation.
        """
        # sudo: a plain employee cannot read an action record, exactly as in
        # _creation_steps and for the same reason.
        action = menu.sudo().action
        if not action:
            return ""
        name = getattr(action, "res_model", False)
        if not name and getattr(action, "model_id", False):
            name = action.model_id.model
        return name if name and name in self.env else ""

    @api.model
    def _primary_button(self, menu):
        """The header button a record just saved on this screen would show.

        Every header button in Odoo carries a condition — ``state != 'draft'``
        and its like — so a rule that skipped conditional buttons skipped all
        of them, which is how "confirm the order" stayed a sentence with no
        step behind it. The condition is read instead: evaluated against the
        values a new record on this screen actually starts with, including the
        defaults the action itself sets. A button whose condition cannot be
        settled that way is left alone, so the step is still derived rather
        than guessed.
        """
        model_name = self._menu_model(menu)
        if not model_name:
            return None
        action = menu.sudo().action
        model = self.env[model_name]
        try:
            arch = etree.fromstring(model.get_view(
                view_id=self._view_id(action, "form"), view_type="form")["arch"])
        except Exception:
            return None

        for header in arch.iter("header"):
            for node in header.iter("button"):
                name, label = node.get("name"), node.get("string")
                if not name or not label or node.get("groups"):
                    continue
                if node.get("type") not in (None, "object"):
                    continue
                if not any(verb in name for verb in _FORWARD_ACTIONS):
                    # Printing, emailing and cancelling are all things a header
                    # offers and none of them is the task. Matched on the
                    # method name rather than the label, which is translated.
                    continue
                if not self._shown_on_a_saved_record(
                        node.get("invisible"), model, action):
                    continue
                return {
                    "trigger": 'button[name="%s"]' % name,
                    "content": _("Press %s to complete the step.", label),
                    "run": "click",
                }
            break  # The first header is the record's own; later ones are not.
        return None

    @api.model
    def _shown_on_a_saved_record(self, condition, model, action):
        """Whether a display condition leaves the thing on screen.

        Unsettled means no: a condition resting on a computed field this code
        cannot read is an element that may not be there, and pointing at one
        that is not is the fault worth avoiding here.
        """
        return self._condition_holds(condition, model, action) is False

    @api.model
    def _condition_holds(self, condition, model, action, reading="record"):
        """True, False, or None when it cannot be settled from here.

        Odoo's modifiers are expressions over a record's own values. There is
        no record yet, so the values a new one on this screen starts with are
        used instead — the field defaults, under the context the action itself
        sets, since a vendor bill opens with its move_type already decided and
        reading a condition without that gets the sales answer.

        None rather than a guess: every caller decides for itself which way to
        fail, and both of them fail towards leaving a step out.
        """
        if not condition:
            return False
        try:
            names = {node.id for node in ast.walk(ast.parse(condition, mode="eval"))
                     if isinstance(node, ast.Name)}
            if names - set(model._fields):
                return None
            context = safe_eval(action.context or "{}", {"uid": self.env.uid,
                                                         "context_today": lambda: False})
            # A record in memory rather than a bag of defaults. Half of what a
            # modifier tests is computed — an operation type hides its
            # reservation method behind hide_reservation_method, which no
            # default_get will ever mention — and reading those as False said
            # "visible" about a field the screen does not draw. A new record
            # runs the computes, which is what the form itself is looking at.
            # Nothing is written: new() never touches the database.
            scoped = model.with_context(**context)
            if reading == "defaults":
                # What the record starts as before anything computes. Coarser,
                # and for some fields closer to what the form ends up showing:
                # a bill's extract_can_show_send_button reads True on a bare
                # new record and False on the one the client builds, and that
                # one difference decides whether the bill demands its date.
                defaults = scoped.default_get(sorted(names))
                values = {name: defaults.get(name, False) for name in names}
            else:
                try:
                    fresh = scoped.new({})
                    values = {}
                    for name in sorted(names):
                        value = fresh[name]
                        if isinstance(value, models.BaseModel):
                            # A link in a condition is tested for being set,
                            # and an unsaved record's id is a placeholder
                            # rather than a number.
                            value = bool(value)
                        values[name] = value
                except Exception:
                    # A compute that will not run — it wants a module that is
                    # not loaded, or a company this user has no access to. The
                    # coarser reading is still an answer, and giving up here
                    # would drop a field that is plainly on the screen.
                    defaults = scoped.default_get(sorted(names))
                    values = {name: defaults.get(name, False) for name in names}
            # Saved, so it has one. Conditions guarding against the unsaved
            # record — "not id or ..." — are past by the time these steps run.
            if "id" in values:
                values["id"] = 1
            return bool(safe_eval(condition, dict(values)))
        except Exception:
            return None

    @api.model
    def _fillable_here(self, node, model, action):
        """Whether this node is on the page and open to being typed in.

        The node's own condition is not the whole story. An invoice draws its
        number twice — once conditionally, once inside a container the form
        hides — and reading only the second said "fill in Number" on a screen
        with no such box, which a browser found on the first walkthrough it
        walked. Every ancestor's condition counts, and a field the form marks
        read-only is not something anybody fills.
        """
        if node.get("readonly") in ("1", "True", "true"):
            return False
        if any(parent.tag == "field" for parent in node.iterancestors()):
            # A column of an embedded table, not a field of this form — the
            # rule _on_the_open_page already states, and the reason an invoice
            # offered its number: the second of its two nodes is a column of
            # the lines below.
            return False
        for element in [node] + list(node.iterancestors()):
            for attribute in ("invisible", "column_invisible"):
                condition = (element.get(attribute) or "").strip()
                if condition and not self._shown_on_a_saved_record(
                        condition, model, action):
                    return False
        page = next((parent for parent in node.iterancestors()
                     if parent.tag == "page"), None)
        if page is not None:
            # Behind a tab nobody has opened yet, which is the knowledge the
            # walkthrough exists to supply rather than to assume.
            notebook = next((parent for parent in page.iterancestors()
                             if parent.tag == "notebook"), None)
            if notebook is not None and list(notebook)[0] is not page:
                return False
        return True

    @api.model
    def _screen_domain(self, menu):
        """The filter the screen behind this menu applies before it draws.

        Only what the action states outright. A server action computes its
        domain in code this cannot read, and reading nothing there is honest:
        the count is then over the whole model, which is the same guarantee
        this had before, no weaker.
        """
        action = menu.sudo().action
        expression = getattr(action, "domain", "") or ""
        if not expression or not isinstance(expression, str):
            return []
        try:
            domain = safe_eval(expression, {
                "uid": self.env.uid,
                "user": self.env.user,
                "allowed_company_ids": self.env.companies.ids,
                "context_today": lambda: fields.Date.context_today(self),
                "datetime": safe_eval_datetime,
                "time": safe_eval_time,
            })
            return domain if isinstance(domain, list) else []
        except Exception:
            # A domain resting on a context key nobody set here cannot be
            # honoured, and guessing one would be worse than counting wide.
            return []

    @api.model
    def _menu_model(self, menu):
        """Which model a menu's screen is about.

        Half the menus that matter open a server action rather than a window —
        Inventory's Receipts is one — and reading ``res_model`` off those
        returns nothing, which is why a stage on them stopped at navigation.
        """
        # sudo: a plain employee cannot read an action record, exactly as in
        # _creation_steps and for the same reason.
        action = menu.sudo().action
        if not action:
            return ""
        name = getattr(action, "res_model", False)
        if not name and getattr(action, "model_id", False):
            name = action.model_id.model
        return name if name and name in self.env else ""

    @api.model
    def _primary_button(self, menu):
        """The header button a record just saved on this screen would show.

        Every header button in Odoo carries a condition — ``state != 'draft'``
        and its like — so a rule that skipped conditional buttons skipped all
        of them, which is how "confirm the order" stayed a sentence with no
        step behind it. The condition is read instead: evaluated against the
        values a new record on this screen actually starts with, including the
        defaults the action itself sets. A button whose condition cannot be
        settled that way is left alone, so the step is still derived rather
        than guessed.
        """
        model_name = self._menu_model(menu)
        if not model_name:
            return None
        action = menu.sudo().action
        model = self.env[model_name]
        try:
            arch = etree.fromstring(model.get_view(
                view_id=self._view_id(action, "form"), view_type="form")["arch"])
        except Exception:
            return None

        for header in arch.iter("header"):
            for node in header.iter("button"):
                name, label = node.get("name"), node.get("string")
                if not name or not label or node.get("groups"):
                    continue
                if node.get("type") not in (None, "object"):
                    continue
                if not any(verb in name for verb in _FORWARD_ACTIONS):
                    # Printing, emailing and cancelling are all things a header
                    # offers and none of them is the task. Matched on the
                    # method name rather than the label, which is translated.
                    continue
                if not self._shown_on_a_saved_record(
                        node.get("invisible"), model, action):
                    continue
                return {
                    "trigger": 'button[name="%s"]' % name,
                    "content": _("Press %s to complete the step.", label),
                    "run": "click",
                }
            break  # The first header is the record's own; later ones are not.
        return None

    @api.model
    def _stage_creates(self, stage):
        """The model a stage exists to create a record of, if it is one.

        What makes a stage skippable: somebody who opens a record of this model
        instead of making one has already done what the stage teaches.
        """
        if not stage.get("create"):
            return ""
        # sudo: reading which model a menu opens, exactly as _creation_steps
        # does and for the same reason.
        action = stage["menu"].sudo().action
        return getattr(action, "res_model", "") or ""

    @api.model
    def _navigation_steps(self, path, opening=True):
        """Click the app, then each menu down to the one that was asked for.

        Matched on the external id alone. Odoo's own clickbot narrows this to
        ``a.o_app.o_menuitem``, but a theme that renders the app tile as a
        ``div`` — which is what this instance does — leaves such a step
        pointing at nothing, and a tour whose trigger never resolves waits
        silently forever. The id is the part no theme changes, and the tour
        engine takes the first visible match, so the same step works whether
        the entry is a tile in the home screen or a row in the navbar.
        """
        steps = []
        if not opening:
            # Inside an app, the navbar shows that app's own menus and nothing
            # else — the other apps' roots are not on the page at all. A stage
            # that crosses into another app therefore has to go back out
            # first, and a walkthrough that pointed straight at the next app's
            # root waited forever on an element that was never coming. Going
            # via the apps menu costs one click and works whether the next
            # stage is in this app or another one.
            steps.append({
                "trigger": ".o_menu_toggle",
                "content": _("Go back to the apps."),
                "run": "click",
            })
        for index, (menu, xmlid) in enumerate(self._clickable_path(path)):
            if index:
                content = _("Then %s.", menu.name)
            elif opening:
                content = _("Open %s.", menu.name)
            else:
                # A later stage starts wherever the last one finished, which is
                # usually a form somebody has just saved. Saying "open" again
                # reads like the walkthrough lost its place.
                content = _("Now go to %s.", menu.name)
            steps.append({
                "trigger": '[data-menu-xmlid="%s"]' % xmlid,
                "content": content,
                "run": "click",
            })
        return steps

    @api.model
    def _creation_steps(self, menu, named=()):
        """New, the fields that must be filled, then save.

        ``named`` are fields the planner asked to point out beyond the required
        ones — a cost, a route, the lines of a bill of materials. They are the
        difference between showing somebody the screen and showing them the
        task, and every one of them is checked against the form the same way a
        required field is, so a name the planner invented simply disappears.
        """
        # sudo: a plain employee cannot read ir.actions.act_window, so asking
        # a question about a menu they can see raised an AccessError instead of
        # answering. What is read here is which model the menu opens and which
        # view it opens first — description of a screen the user has already
        # been shown they may reach. The gates that matter are untouched: the
        # menu had to survive _filter_visible_menus as them, and the New button
        # is still offered only if they may create the record.
        action = menu.sudo().action
        model_name = getattr(action, "res_model", False)
        if not model_name or model_name not in self.env:
            return []

        model = self.env[model_name]
        add_button = self._add_button(model, action)
        if not add_button:
            return []
        if not model.has_access("create"):
            # Pointing someone at a New button they do not have is not help.
            return []

        steps = [{
            "trigger": add_button,
            "content": _("Press New."),
            "run": "click",
        }]
        wanted = self._required_visible_fields(model, action)
        already = {name for name, dummy in wanted}
        wanted += [
            (name, label) for name, label in self._named_fields(model, named, action)
            if name not in already
        ]
        if not wanted:
            # New, then Save, and nothing in between. Some forms mark nothing
            # required in their arch — a vendor is one — so a stage that exists
            # to create something showed the user two buttons and no work.
            # Saving then fails on a record with no name, which reads as the
            # walkthrough being wrong rather than incomplete. Whatever the
            # model calls a record by is the field that is really needed.
            wanted = self._naming_field(model, action) \
                or self._first_fillable_field(model, action)
        for name, label in wanted:
            steps.append({
                "trigger": self._field_trigger(name),
                "content": _("Fill in %s.", label),
                "run": "click",
            })
        steps.append({
            "trigger": ".o_form_button_save",
            "content": _("Save once the required fields are filled."),
            "run": "click",
        })
        return steps

    @api.model
    def _field_trigger(self, name):
        """Point at the control the user has to touch, not at the box round it.

        ``[name="x"]`` is the field's widget, which is as wide as the column
        even when what the user must click is a small thing at one end of it.
        The pointer is placed over the middle of whatever the trigger resolves
        to, so on a radio field it lands in the empty half of the box. Measured
        on a product form in Arabic: the widget spans x 656–1124 while the three
        radios occupy 914–1124, putting the pointer at 890 — outside the
        controls altogether. The same form in English hides it, because there
        the controls sit at the end the box starts at.

        ``findTriggers`` splits an anchor on its commas and takes the first
        part that matches, so a second part is a fallback rather than a rival:
        a widget that draws no control of its own still resolves. And the click
        is listened for on every part that matched, so pressing the widget
        counts too.
        """
        return (
            '.o_form_view [name="%(name)s"] '
            ':is(input:not([type=hidden]),textarea,select,[contenteditable="true"])'
            ', .o_form_view [name="%(name)s"]'
        ) % {"name": name}

    @api.model
    def _add_button(self, model, action):
        """The New button on the screen this action opens, or nothing.

        Which button creates a record depends on the view that opens, and the
        view that opens is the first one the action names — not whichever ones
        it also allows. An action opening straight into a form — every Settings
        screen, most wizards — creates nothing and gets no such step at all.

        A kanban is the interesting one. Its New sometimes drops an editable
        card into the column instead of opening a form, and the fields a
        walkthrough goes on to name are then nowhere on the page. That is worth
        refusing — but it is not what every kanban does. The web client takes
        the inline path only when the arch asks for it by name (see
        ``KanbanController.createRecord``: ``onCreate === "quick_create"``), and
        Products, the screen somebody asking how to build a manufactured item
        has to start on, does not. Refusing every kanban dropped the first
        stage of exactly the tasks that need one.
        """
        # ``views`` rather than ``view_mode``: an action's view_ids override the
        # order view_mode declares, and the web client follows views. Products
        # is the case that matters — view_mode says "list,form" while views
        # puts kanban first, so a walkthrough built from view_mode pointed at a
        # list's New button on a screen showing a kanban, and stopped there.
        views = getattr(action, "views", None) or []
        if views:
            opens = views[0][1]
        else:
            opens = (getattr(action, "view_mode", "") or "").split(",")[0].strip()
        if opens in ("list", "tree"):
            return ".o_list_button_add"
        if opens != "kanban":
            return None

        try:
            # The action's own kanban, for the reason the form reader takes
            # the action's own form: the Accounting dashboard is a kanban over
            # account.journal that is not the model's default one, and reading
            # the default said its New opens a form when what it opens is the
            # dashboard the walkthrough was already standing on.
            arch = etree.fromstring(model.get_view(
                view_id=self._view_id(action, "kanban"),
                view_type="kanban")["arch"])
        except Exception:
            # Without the arch there is no telling which kind of New this is,
            # and guessing wrong points a step at a form that never opens.
            return None
        if (arch.get("on_create") or "").strip():
            # "quick_create" is the inline card. Anything else names an action
            # — a wizard whose fields are its own, not this model's — and the
            # fields this walkthrough would go on to name are not on it either.
            return None
        return ".o-kanban-button-new"

    @api.model
    def _on_the_open_page(self, node, model=None, action=None):
        """Whether a field is on the page the form opens showing.

        A notebook only draws the tab that is open; the rest exist in the arch
        and nowhere on the screen. An employee's marital status and type sit on
        a later tab, so a walkthrough naming them pointed at nothing until
        somebody had already clicked through — which is precisely the knowledge
        the walkthrough was supposed to supply.

        A field nested inside another field is a column of an embedded table,
        not a field of this form. ``quantity`` and ``analytic_distribution``
        under an invoice's lines exist in the arch and draw nothing until
        somebody adds a row — walking a customer invoice on the live database
        found both pointing at nothing. The table itself is the thing to point
        at, and it is already offered as the one2many field it belongs to.

        Fields outside a notebook, and those on the first page of one, are
        there the moment the form opens.
        """
        if any(parent.tag == "field" for parent in node.iterancestors()):
            return False

        # A modifier decides at runtime whether the field is drawn at all, and
        # what it depends on is the record in front of the user — which does
        # not exist yet when the walkthrough is built. Walking a new expense on
        # the live database found two of them: "Customer to Reinvoice" carries
        # invisible="not can_be_reinvoiced", and Quantity sits in a div marked
        # invisible="not product_has_cost". Both resolved in the arch and drew
        # nothing. Not knowing whether a step will appear is reason enough to
        # leave it out; a walkthrough is shorter for it and never points at
        # empty space.
        for element in (node, *node.iterancestors()):
            for attribute in ("invisible", "column_invisible"):
                condition = (element.get(attribute) or "").strip()
                if not condition or condition in ("0", "False"):
                    continue
                if model is None or self._condition_holds(
                        condition, model, action) is not False:
                    # Either nothing to evaluate against, or the condition
                    # holds, or it cannot be settled. All three mean the field
                    # may not be drawn, and a step pointing at one that is not
                    # there is the fault this whole rule exists to prevent.
                    return False

        page = next(
            (parent for parent in node.iterancestors() if parent.tag == "page"),
            None,
        )
        if page is None:
            return True
        notebook = page.getparent()
        if notebook is None:
            return True
        pages = [child for child in notebook if child.tag == "page"]
        return bool(pages) and pages[0] is page

    @api.model
    def _required_visible_fields(self, model, action=None):
        """Required fields that the form view actually puts on screen.

        A field the model demands but the view never shows cannot be pointed
        at, and one the view shows but nobody has to fill is noise.
        """
        shown, demanded = self._form_fields(model, action)
        described = model.fields_get(shown, ["string", "required", "readonly"])
        out = []
        for name in shown:
            info = described.get(name)
            if not info or info.get("readonly"):
                continue
            if not (info.get("required") or name in demanded):
                continue
            if any(name == existing for existing, dummy in out):
                continue
            out.append((name, info.get("string") or name))
        return out

    @api.model
    def _named_fields(self, model, names, action=None):
        """The fields of ``names`` this form really draws, in the given order.

        The planner is told not to guess a field name and told that guesses are
        dropped, but being told is not a guarantee. This is the guarantee: a
        name that is not on the form the user is about to see never becomes a
        step, so the worst a wrong guess costs is a step that is not there.
        """
        if not names:
            return []
        shown, dummy = self._form_fields(model, action)
        on_the_form = [name for name in names if name in shown]
        if not on_the_form:
            # fields_get() reads an empty list as "no filter" and answers with
            # every field on the model, so the guard inverted precisely when it
            # was doing its job: none of the planner's names survived, and all
            # of them came back. A walkthrough for an expense then pointed at
            # Analytic Distribution, a field that form does not draw at all.
            return []
        described = model.fields_get(on_the_form, ["string", "readonly"])
        out = []
        for name in names:
            info = described.get(name)
            if not info or info.get("readonly"):
                continue
            if any(name == existing for existing, ignored in out):
                continue
            out.append((name, info.get("string") or name))
        return out

    @api.model
    def _form_view_id(self, action):
        """The form view this action opens, when it names one."""
        return self._view_id(action, "form")

    @api.model
    def _view_id(self, action, mode):
        """The view of ``mode`` this action opens, when it names one.

        A model can have several form views and an action chooses among them.
        Reading the model's default instead means reading a screen the user is
        not going to see: hr.leave draws employee_id twice in its default form,
        once invisible, while the action behind My Time Off opens a different
        view entirely — so a step was written for a field that was not on the
        page it landed on, and it pointed at nothing.
        """
        if not action:
            return None
        view = getattr(action, "view_id", False)
        if view and view.type == mode:
            return view.id
        for view_id, view_mode in (getattr(action, "views", None) or []):
            if view_mode == mode and view_id:
                return view_id
        return None

    @api.model
    def _form_fields(self, model, action=None):
        """``(names the form draws on opening, names the view marks required)``.

        One reading of the arch, because two callers need the same answer to
        the same question: which fields are actually on the page in front of
        the user the moment the form opens.
        """
        try:
            view = model.get_view(
                view_id=self._form_view_id(action), view_type="form")
        except Exception:  # a model without a form view is not a failure here
            return [], set()

        arch = etree.fromstring(view["arch"])
        shown = []
        # A view can also make an optional field required, so what it says is
        # collected rather than only used to veto.
        demanded = set()
        for node in arch.iter("field"):
            name = node.get("name")
            if not name:
                continue
            # A field inside another field is part of an embedded subview — the
            # columns of a one2many list, the form behind a many2one. It
            # belongs to a different record on a different screen, and it is
            # not on this form at all. mrp.production is where this showed:
            # picking_type_id, location_dest_id and company_id all appear
            # inside the list of stock moves with nothing marking them hidden,
            # while their real places on the form are either invisible or on a
            # later tab. A walkthrough told somebody to fill in three fields
            # that were not there.
            if any(parent.tag == "field" for parent in node.iterancestors()):
                continue
            # A view may hide a field on a condition rather than outright.
            # This used to drop every one of them, on the grounds that the
            # condition is about a record that does not exist yet and a step
            # pointing at a field that may not be drawn is a coin toss.
            #
            # It is not a coin toss any more. The condition is evaluated
            # against the values a new record on this screen starts with,
            # including the defaults the action itself sets — and where it
            # cannot be settled the field is still dropped, so nothing is
            # pointed at on a guess. What the blanket rule cost: a vendor bill
            # hides its partner and its date behind move_type, so the whole
            # screen came out with no fields at all, and Odoo answered the
            # walkthrough with "Missing required fields".
            if node.get("invisible") and self._condition_holds(
                    node.get("invisible"), model, action) is not False:
                continue
            # The view has the last word over the model on both of these.
            # crm.team declares alias_id required and its form then sets
            # required="0" and marks it read-only, so a walkthrough built from
            # the model alone told people to fill a field they cannot type in.
            if node.get("required") in ("0", "False"):
                continue
            if node.get("readonly") in ("1", "True") \
                    or "oe_read_only" in (node.get("class") or ""):
                continue
            if node.get("widget") in _DECORATIVE_WIDGETS:
                continue
            if not self._on_the_open_page(node, model, action):
                continue
            asked = (node.get("required") or "").strip()
            if asked in ("1", "True"):
                demanded.add(name)
            elif asked and (
                    self._condition_holds(asked, model, action) is True
                    or self._condition_holds(asked, model, action, "defaults") is True):
                # Required on a condition that holds under either reading of a
                # new record. The two disagree about a vendor bill's date, and
                # the cost of the disagreement is not symmetric: an extra field
                # step is one harmless click on a box that is on the screen,
                # while a missing required one is a record that will not save
                # and a walkthrough that stops at Save. So either saying yes is
                # enough. Odoo answered "Missing required fields" in a browser,
                # which is the only place that sentence appears.
                demanded.add(name)
            shown.append(name)

        return shown, demanded

    # ------------------------------------------------------------------
    # Storing it
    # ------------------------------------------------------------------

    @api.model
    def _create_chain(self, question, menus, stages):
        """One tour per stage, chained, and only the first one offered.

        A stage is the unit a walkthrough can be adapted at. Somebody who
        already has the raw materials should not be walked through creating
        them, and a single tour cannot express that: the interactive engine is
        a linear queue of steps with no way to leave one out. Separate tours
        can be, because ``consume`` decides which one comes next.

        The seam is invisible when every stage is wanted — the client starts
        what ``consume`` returns without a redirect — so this costs nothing in
        the ordinary case.

        Only the head is matchable. A middle stage offered on its own would
        answer a question with the tail of its own answer.
        """
        chain = []
        following = self.env["web_tour.tour"]
        for index, (menu, steps, creates) in reversed(list(enumerate(stages))):
            first = index == 0
            following = self._create_tour(
                question,
                # The head carries every menu the whole walkthrough visits, so
                # the offer is withheld from anyone who cannot reach all of it.
                # A later stage carries only its own: by then the user is being
                # handed a screen, not offered a task.
                menus if first else menu,
                steps,
                offered=first,
                following=following,
                creates=creates,
            )
            chain.append(following)

        head = chain[-1]
        if len(chain) > 1:
            head.browse([t.id for t in chain[:-1]]).sudo().write(
                {"assistant_first_stage_id": head.id})
        return head

    @api.model
    def _create_tour(self, question, menus, steps, offered=True, following=None,
                     creates=""):
        """Save the steps as a tour only the right people are offered."""
        # sudo: tours are writable by administrators only, and this one is
        # assembled from metadata the asking user was already allowed to read.
        tours = self.env["web_tour.tour"].sudo()
        name = self._unique_name(question)
        return tours.create({
            "name": name,
            "url": "/odoo",
            "custom": True,
            "assistant_enabled": offered,
            "assistant_next_stage_id": following.id if following else False,
            "assistant_skip_model": creates,
            "assistant_generated": True,
            # Unstamped is older than everything, so a walkthrough without this
            # is built, answers the question that produced it once, and is then
            # never offered again and swept within the day. The line that was
            # meant to put it here did not apply, and nothing noticed until
            # three hundred questions were asked and thirty-seven of the
            # walkthroughs came back unstamped.
            "assistant_builder_version": BUILDER_VERSION,
            "assistant_description": question,
            # Every menu it walks through, so the tour is only ever offered to
            # somebody who can reach all of them. A walkthrough that spans
            # stock and manufacturing is no use to a viewer who has one of
            # them, and would hand them an access error halfway rather than
            # help — see ``_assistant_is_visible_to_user``.
            "assistant_menu_ids": [(6, 0, menus.ids)],
            "assistant_group_ids": [(6, 0, menus.group_ids.ids)],
            # Only the stage that ends the walkthrough celebrates. A rainbow
            # between two stages reads as "done" to somebody who is not.
            "rainbow_man_message": (
                False if following else _("<b>That is the way there.</b>")
            ),
            "step_ids": [
                (0, 0, dict(step, sequence=(index + 1) * 10))
                for index, step in enumerate(steps)
            ],
        })

    @api.model
    def _unique_name(self, question):
        """web_tour.tour names are unique, so settle it before creating.

        Hashed with md5 rather than hash(): the built-in is salted per
        process, so the same question would name its tour differently on
        every worker.
        """
        digest = hashlib.md5(
            text_match.normalize(question).encode("utf-8")
        ).hexdigest()[:10]
        base = "assistant_generated_%s" % digest
        tours = self.env["web_tour.tour"].sudo()
        name, suffix = base, 1
        while tours.search_count([("name", "=", name)]):
            suffix += 1
            name = "%s_%s" % (base, suffix)
        return name

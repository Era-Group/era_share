# -*- coding: utf-8 -*-
"""Choosing a menu when word overlap cannot.

Matching on the words a question and a menu share is predictable and cheap,
and it answers most questions. It fails in two ways that no amount of stemming
fixes, both of them measured against a database carrying 221 modules and 293
menus a user can reach:

*Ambiguity.* "اضافة عميل" agrees completely with nine menus across six apps.
The builder refuses rather than guess, which is right — but the user asked a
perfectly clear question and got nothing.

*Vocabulary.* Somebody asks for a "تذكرة دعم" and the menu is called "مكتب
المساعدة". No word is shared, so nothing is even a candidate.

Both are questions about language, which is what a model is for. So it is asked
to *choose*, never to invent: it is handed the menus this user can actually
reach in this database and must answer with one of their ids. An answer that is
not on the list it was given is discarded — the guarantee that a step points at
something real is kept in code, not asked of the model.
"""

import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Generous on purpose. The ambiguous case puts the right menu near the front,
# so a short list would do — but the case where no word is shared scores every
# menu at zero, and the right one is then as likely to be last as first.
# Trimming to a shortlist would answer the easy half of the problem and quietly
# lose the other.
#
# 250 was already short of one ordinary database: the instance this was built
# on offers 348, so 98 menus were never shown to the planner at all, and a
# question about any of them could only ever be answered wrongly or refused.
# Nothing said so. The cap is now above what a fully loaded Odoo carries, and
# reaching it is logged rather than passed over — a limit on coverage that
# leaves no trace reads as complete coverage, which is the worse failure.
MAX_CANDIDATES = 600


class TourAssistantPlanner(models.AbstractModel):
    _name = "tour.assistant.planner"
    _description = "Tour Assistant Planner"

    # ------------------------------------------------------------------
    # The account
    # ------------------------------------------------------------------

    @api.model
    def _enabled(self):
        """Whether the model may be asked at all.

        On by default, and a no-op without a usable account, so installing the
        module never starts spending on its own.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.ai_planner", "True"
        )
        return str(raw).strip().lower() not in ("0", "false", "no", "off")

    @api.model
    def _account(self):
        """A usable AI account, or an empty recordset.

        Deliberately provider-agnostic: ``era_ai_accounts`` already fronts a
        local CLI, an OpenAI-compatible endpoint and several vendors behind one
        ``generate_text``. Which one a deployment points at is a decision for
        whoever configures it, not for this module.

        sudo: the account holds a credential a user must never read, and the
        only thing done with it here is one call whose prompt this module wrote.
        """
        if "era.ai.account" not in self.env:
            return None
        accounts = self.env["era.ai.account"].sudo()
        domain = [("state", "=", "valid"), ("active", "=", True)]

        # Which account answers questions is a decision with a bill attached,
        # and a database can hold several. Naming one in
        # ``era_web_tour_assistant.ai_account`` settles it; without that the first
        # valid one wins, which is an arbitrary way to choose who pays.
        wanted = (self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.ai_account", ""
        ) or "").strip()
        if wanted:
            named = accounts.search(domain + [("name", "=", wanted)], limit=1)
            if named:
                return named
            # Falling through to any account would send the questions somewhere
            # nobody asked for. Better to answer nothing and have the mistake
            # noticed.
            _logger.warning(
                "Tour Assistant: no usable AI account named %r.", wanted
            )
            return None
        return accounts.search(domain, limit=1) or None

    @api.model
    def available(self):
        """Whether a question can be sent anywhere."""
        return bool(self._enabled()) and bool(self._account())

    # ------------------------------------------------------------------
    # Noticing when it stops working
    # ------------------------------------------------------------------

    @api.model
    def check_account(self):
        """Ask the account something trivial, and say so loudly if it cannot.

        A revoked or expired login does not break anything visibly: the planner
        answers nothing, questions that used to be answered start reaching the
        queue instead, and what an administrator sees is a slow decline they
        have no reason to connect to a credential. The cheapest question there
        is, once a day, turns that into a line in the log with the account's
        name on it.

        Returns True while it works, so the check can also be run by hand from
        a shell and believed.
        """
        if not self._enabled():
            return True
        account = self._account()
        if not account:
            _logger.warning(
                "Tour Assistant: no usable AI account. Questions the menus "
                "cannot answer are being queued rather than worked out."
            )
            return False
        try:
            answer = account.generate_text(
                "Reply with the single digit 1 and nothing else.",
                system="You reply with a bare digit.",
            )
        except Exception as error:
            _logger.error(
                "Tour Assistant: AI account %r is not answering (%s). Until it "
                "does, only questions the menus can answer will be answered.",
                account.name, error,
            )
            return False
        if not str(answer or "").strip():
            _logger.error(
                "Tour Assistant: AI account %r answered with nothing.",
                account.name,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Choosing
    # ------------------------------------------------------------------

    # A real question is rarely one screen. "How do I make a manufactured
    # product from three raw components, each with a value" is three: the raw
    # materials, the finished product, the bill of materials that ties them
    # together. Answering it with the bill of materials alone is not wrong —
    # it is where the work ends up — but it drops the user at the last step of
    # a task whose first two steps are the ones they did not know.
    #
    # So the model is asked for an ordered plan rather than a destination. It
    # still only ever picks numbers off the list it was handed; what is new is
    # that it may pick several, in order, and say what each one is for.
    MAX_STAGES = 5

    SYSTEM = (
        "You lay out, in order, the screens an Odoo user must visit to do "
        "what they asked, using only the menus you are given.\n"
        "\n"
        "You are given the menus this user can reach in this database, each "
        "with a number and, in brackets, the record it opens. Trust the record "
        "over the wording: Purchase / Orders / Vendors is [res.partner], so it "
        "is where contacts are kept, not where a purchase order is written. "
        "Reply with one line per stage, in the order the user must do them, "
        "and nothing else — no preamble, no numbering of your own, no closing "
        "remark.\n"
        "\n"
        "Each line has four fields separated by |\n"
        "  <menu number> | create or visit | what to do here and why | fields\n"
        "\n"
        "  menu number   one of the numbers above, and nothing else.\n"
        "  create/visit  'create' only if the user makes a brand new record on "
        "this screen — the walkthrough will press New, fill the required "
        "fields and save. Use 'visit' for everything else, including opening "
        "an existing record to change it. Saying 'create' and then describing "
        "opening something that already exists contradicts the steps the user "
        "is shown, and they will follow the steps.\n"
        "  what to do    one sentence, in the language the user asked in, "
        "telling them what this screen is for in their task. Be concrete and "
        "specific to what they asked. If they must repeat the stage — three "
        "raw materials, one at a time — say so here.\n"
        "  fields        optional, comma separated technical field names to "
        "point out on this screen beyond the ones that are already required. "
        "Use them for the fields the task turns on: a cost, a route, a line "
        "list. Leave empty if none. Never guess a name: if you are not sure "
        "the field exists, leave it out. Names you invent are dropped.\n"
        "\n"
        "Give as many stages as the task honestly takes, up to %(stages)d. "
        "Most questions are one stage and should get one. A question that "
        "involves setting something up before using it, or several records "
        "that reference each other, is several — and answering it with the "
        "last stage alone leaves out the part the user did not know.\n"
        "\n"
        "Choose the screen where the work is performed, not a report about it "
        "and not a settings screen that configures it.\n"
        "\n"
        "If you can do part of the task with the menus you were given, plan "
        "that part and say in its last sentence what cannot be done here and "
        "why. Someone who asked how to hire an employee and set their salary, "
        "on an Odoo with staff records but no payroll, should be walked "
        "through creating the employee and told plainly that the salary needs "
        "the payroll app. Half the task and an honest sentence is far more use "
        "than nothing.\n"
        "\n"
        "That licence is to leave a stage out, never to put a different one "
        "in. A stage has to be a screen this task genuinely passes through. A "
        "screen that merely sounds related is a wrong answer the user will "
        "follow, and it costs them more than being told nothing fits.\n"
        "\n"
        "Reply 0 when the task itself is not something Odoo does, even where "
        "a screen for those records is on your list. Turning a document back "
        "into an earlier one — an invoice into a sales order, a receipt into a "
        "purchase order — runs against the direction the flow goes, and "
        "deleting a posted journal entry is not how a posted entry is undone. "
        "Walking somebody to the screen those records live on does not perform "
        "the task, and they will follow the steps, arrive, and find nothing "
        "there to do. Say what the system does instead — which way the flow "
        "runs, or that a posted entry is reversed rather than deleted — in the "
        "sentence on the second line. An honest 'it does not work that way' is "
        "worth more than a walk to a related screen.\n"
        "\n"
        "Reply 0 to a request to destroy or empty records — delete all the "
        "invoices, wipe the customers, clear the year — even when a screen for "
        "those records is on your list. Walking somebody to the list is not an "
        "answer to that question, and a walkthrough that appears to help with "
        "it is worse than one that says no. Say in the sentence that this is "
        "not something to be done from a screen one record at a time, and that "
        "whoever is responsible for the database should be asked. Changing one "
        "record is ordinary work and is planned normally; it is the sweep that "
        "is refused.\n"
        "\n"
        "Reply 0 to a question about this software or about you rather than "
        "about a task in it — what is this program, who are you, how does the "
        "assistant work. There is no screen that answers those, and picking "
        "one whose name happens to sound similar is the worst answer available."
        "\n"
        "\n"
        "Reply 0 only when no menu on the list relates to the question at all. "
        "Put it on the first line, and on a second line one short sentence "
        "telling the user why — in the language they asked in. The usual "
        "reason is that this Odoo does not have the app for it installed, and "
        "saying so plainly is far more use to them than silence. Do not "
        "suggest a menu you were not given. Answering 0 is correct and "
        "expected where nothing fits; a wrong menu sends the user to the wrong "
        "screen trusting it."
    )

    @api.model
    def plan_task(self, question, menus):
        """The ordered stages that answer ``question``, and why there are none.

        Returns ``(stages, reason)``, where a stage is a dict of ``menu`` (a
        record from ``menus``, never anything else), ``goal`` (a sentence to
        show the user), ``create`` (whether a record is made there) and
        ``fields`` (technical names the model asked to point out, unvalidated
        here — the builder drops the ones the screen does not draw).

        The reason is only ever set when the model read the list and decided
        nothing on it answers them, which on a database missing the app they
        are asking about is the most useful thing anybody can tell them.
        """
        if not question or not menus or not self._enabled():
            return [], ""
        account = self._account()
        if not account:
            return [], ""

        offered = menus[:MAX_CANDIDATES]
        if len(menus) > MAX_CANDIDATES:
            _logger.warning(
                "Tour Assistant: %d of %d menus were not offered to the "
                "planner. Questions about them cannot be answered correctly; "
                "raise MAX_CANDIDATES.",
                len(menus) - MAX_CANDIDATES, len(menus),
            )
        prompt = "Question: %s\n\nMenus:\n%s" % (question, self._listing(offered))

        try:
            # temperature=0: the same question has to plan the same way twice.
            # Without it a walkthrough rebuilt after a fix came back naming
            # different fields, and telling "this is fixed" apart from "the
            # model happened to choose otherwise" took a browser and an hour.
            # A user asking again deserves the same answer for the same reason.
            answer = account.generate_text(
                prompt, system=self.SYSTEM % {"stages": self.MAX_STAGES},
                temperature=0,
            )
        except Exception as error:  # a missing menu must never break asking
            _logger.warning("Tour Assistant planner unavailable: %s", error)
            return [], ""

        stages = self._read_plan(answer, offered)
        if not stages:
            # Format drift, which is the failure to expect from a model told to
            # answer in a shape. A bare number is the answer to the question
            # this used to ask, and it is a perfectly good one screen plan —
            # far better than discarding it and telling the user nothing fits.
            index = self._read_choice(answer, len(offered))
            if index:
                stages = [{
                    "menu": offered[index - 1],
                    "goal": "",
                    "create": self.env["tour.assistant.builder"]
                    ._wants_to_create(question),
                    "fields": [],
                }]
        if not stages:
            return [], self._read_reason(answer)
        return stages, ""

    @api.model
    def _listing(self, offered):
        """The menus, numbered, each with the record it opens.

        The full path, because a bare "العملاء" appears under half a dozen
        apps and the app it sits in is exactly what tells them apart. And the
        model, because the path is not always enough: Purchase / Orders /
        Vendors is res.partner, and a plan once sent somebody there to "create
        a purchase order for the vendor". Every word in that menu's name
        agreed with the question; only the model it opens disagreed.

        sudo: reading which model an action names. The menus were already
        filtered to what this user may reach, so nothing is disclosed here that
        clicking the menu would not show them.
        """
        # Read in one go. Six hundred menus each fetching their own action is
        # six hundred queries on the path a user is waiting in front of.
        windows = self.env["ir.actions.act_window"].sudo()
        by_id = {}
        for menu in offered.sudo():
            action = menu.action
            if action and action._name == windows._name:
                by_id.setdefault(action.id, []).append(menu.id)
        models_by_menu = {}
        if by_id:
            for action in windows.browse(list(by_id)).exists():
                for menu_id in by_id[action.id]:
                    models_by_menu[menu_id] = action.res_model or ""

        rows = []
        for index, menu in enumerate(offered, 1):
            name = menu.complete_name or menu.name
            model_name = models_by_menu.get(menu.id)
            rows.append("%d. %s [%s]" % (index, name, model_name) if model_name
                        else "%d. %s" % (index, name))
        return "\n".join(rows)

    @api.model
    def choose(self, question, menus):
        """The single menu that answers ``question``, and why there is none.

        The stage-by-stage plan is the real answer; this is the first stage of
        it, kept because a caller that only wants somewhere to go should not
        have to know what a stage is.
        """
        stages, reason = self.plan_task(question, menus)
        if not stages:
            return self.env["ir.ui.menu"], reason
        return stages[0]["menu"], ""

    # A sentence, not an essay: it is shown in a dialog the user is waiting in
    # front of, and a model given room to explain will fill it.
    MAX_GOAL = 200

    # Enough for the handful of fields a task actually turns on. A model that
    # answers with thirty is listing the form, not answering the question.
    MAX_STAGE_FIELDS = 6

    @api.model
    def _read_plan(self, answer, offered):
        """The stages in ``answer`` that name a menu that was offered.

        Read line by line and defensively: a line that cannot be parsed, or
        names a number that was not on the list, is dropped rather than
        allowed to fail the whole plan. What survives is a list of menus this
        user can reach — the guarantee that a step points at something real is
        kept here, in code, rather than asked of the model.
        """
        stages = []
        seen = set()
        for line in str(answer or "").splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2:
                continue
            index = self._read_choice(parts[0], len(offered))
            if not index:
                continue
            # The same screen twice is the model restating a stage, not a
            # second visit worth walking somebody through.
            if index in seen:
                continue
            seen.add(index)
            goal = parts[2].strip()[:self.MAX_GOAL] if len(parts) > 2 else ""
            fields = []
            if len(parts) > 3:
                fields = [
                    name.strip() for name in parts[3].split(",")
                    if name.strip()
                ][:self.MAX_STAGE_FIELDS]
            stages.append({
                "menu": offered[index - 1],
                "goal": goal,
                "create": parts[1].strip().lower().startswith("create"),
                "fields": fields,
            })
            if len(stages) >= self.MAX_STAGES:
                break
        return stages

    # A sentence, not an essay: it is shown in a dialog the user is waiting in
    # front of, and a model given room to explain will fill it.
    MAX_REASON = 300

    @api.model
    def _read_reason(self, answer):
        """The explanation offered alongside a refusal, if there was one.

        Taken as plain text and shown as plain text. It never becomes a step,
        a selector or a link, so the worst a careless sentence can do is read
        oddly — which is a far smaller price than leaving somebody staring at
        a queue message that tells them nothing.
        """
        lines = [line.strip() for line in str(answer or "").splitlines()]
        # The first line carries the number; anything after it is the reason.
        wanted = [line for line in lines[1:] if line and not line.isdigit()]
        if not wanted:
            return ""
        return " ".join(wanted)[:self.MAX_REASON].strip()

    @api.model
    def _read_choice(self, answer, count):
        """The number the model answered with, or 0.

        Read leniently — a model told to reply with a bare number still
        sometimes wraps it in a sentence — but never leniently enough to accept
        a number that was not offered.
        """
        digits = ""
        for char in str(answer or ""):
            if char.isdigit():
                digits += char
            elif digits:
                break
        if not digits:
            return 0
        try:
            index = int(digits)
        except ValueError:
            return 0
        return index if 1 <= index <= count else 0

#!/usr/bin/env python3
"""Walk generated walkthroughs in a real browser and report what does not show.

The builder guarantees that every trigger it writes is an external id or a
field name the database holds. That is a guarantee about the database, not
about the screen: a menu can exist and be hidden behind a group, a required
field can be on a tab that has not been opened, a button can be absent until a
record is selected. A step like that resolves perfectly in SQL and leaves the
user staring at a pointer aimed at nothing.

Nothing here writes to Odoo. It opens each tour's steps in order, asks the page
whether the trigger matches anything visible, and prints what did not. Run it
against a scratch copy — walking a walkthrough clicks the things it points at,
and pointing at "New" then "Save" is how records get created.

    export SCRATCH_URL=http://127.0.0.1:8070
    export SCRATCH_DB=tour_scratch
    export SCRATCH_LOGIN=admin SCRATCH_PASSWORD=...
    python3 verify_tours.py            # every generated tour
    python3 verify_tours.py --limit 5  # the five most recent
    python3 verify_tours.py --only X   # one, in a browser of its own
    python3 verify_tours.py --fill     # type into the fields and check it saves

Two questions, and they are not the same one. Without --fill the walk measures
whether every step appears — a field step clicks the field and types nothing,
so Save refuses the form and every walkthrough ends alike whether or not it
teaches the work. With it, a walkthrough has to end on a record that really
saved, which is the only evidence the task can be finished by following it.
--fill creates records, so it belongs on a scratch copy and nowhere else.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

BROWSER_TIMEOUT = 90

# How long a step is given to appear before it is called missing. A menu that
# needs a page to load and an app to render is not late at two seconds, and a
# fixed pause instead of a wait is how a working walkthrough gets reported as
# broken — which is exactly what the first version of this script did.
APPEAR_MS = "10000"


def browser_binary():
    return (
        os.environ.get("AGENT_BROWSER")
        or shutil.which("agent-browser")
        or "agent-browser"
    )


class Page:
    """Just enough of the agent-browser CLI to look at a page."""

    def __init__(self, session="tour-verify"):
        self.binary = browser_binary()
        self.session = session

    def run(self, *args, check=True):
        result = subprocess.run(
            [self.binary, "--session", self.session, *args],
            capture_output=True, text=True, timeout=BROWSER_TIMEOUT,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                "browser %s failed: %s" % (args[0], result.stderr.strip()[:300])
            )
        return result.stdout.strip()

    def open(self, url):
        self.run("open", url)
        self.run("wait", "--load", "networkidle", check=False)

    def login(self, url, login, password):
        self.open(url.rstrip("/") + "/web/login")
        self.run("fill", "input[name=login]", login)
        self.run("fill", "input[name=password]", password)
        # Not by clicking the button: once the website module is installed the
        # login page gains a sticky header, which sits over the button and
        # makes the click land on the header instead. Enter in the password
        # field is the usual way round that, but it does not submit here at
        # all — the page simply stays put, and the run then depended on a
        # session cookie left by some earlier login. Submitting the form is
        # what actually works, and it fails loudly when it does not.
        # The form holding the login field, not the first form on the page:
        # this page can carry more than one, and submitting the wrong one
        # leaves the browser on the login screen with no complaint.
        self.run(
            "eval",
            "document.querySelector('input[name=login]').closest('form').submit()",
            check=False,
        )
        self.run("wait", "--load", "networkidle", check=False)
        if "login" in self.run("eval", "location.pathname", check=False):
            raise SystemExit(
                "the browser could not log in as %s — every step after this "
                "would be reported missing against a login page" % login
            )

    def visible(self, selector):
        """Whether the trigger matches something a user could actually act on.

        Present in the DOM is not the test — a menu inside a collapsed drawer
        and a field on an unopened tab are both there and both useless to
        somebody being pointed at them.
        """
        script = (
            "(() => { const el = document.querySelector(%s);"
            " if (!el) return 'missing';"
            " const box = el.getBoundingClientRect();"
            " if (!box.width || !box.height) return 'hidden';"
            " const style = getComputedStyle(el);"
            " if (style.visibility === 'hidden' || style.display === 'none')"
            "   return 'hidden';"
            " return 'ok'; })()"
        ) % json.dumps(selector)
        return self.run("eval", script, check=False).strip().strip('"')

    def await_element(self, selector):
        """Give the page a chance to produce the step, and bring it on screen.

        Scrolling matters as much as waiting. An app icon two rows below the
        fold is present, sized and perfectly clickable to a person, but a click
        aimed at its centre lands outside the window and does nothing — which
        reads as a broken step and is not one. The tour runner scrolls before
        it points; so must anything checking its work.
        """
        self.run("wait", selector, "--timeout", APPEAR_MS, check=False)
        self.run("scrollintoview", selector, check=False)

    def where(self):
        """A short description of the page, for a report about a step on it."""
        script = (
            "(() => {"
            " const heading = document.querySelector('.o_breadcrumb, .o_control_panel h1, h1');"
            " return location.pathname + (heading ? ' — ' + heading.innerText.trim().slice(0, 40) : '');"
            " })()"
        )
        return self.run("eval", script, check=False).strip().strip('"')

    def fill(self, selector):
        """Put a plausible value in whatever the step is pointing at.

        Without this the walk measures whether steps appear, not whether the
        task can be finished: a field step clicks the field and types nothing,
        so Save refuses the form and every walkthrough ends the same way
        whether or not it actually teaches the work. A walkthrough can point at
        all the right things and still not carry anybody through.

        The value only has to be accepted, not to be sensible — a saved record
        is the measurement.
        """
        script = (
            "(() => {"
            # The engine takes the first *visible* match; taking the first
            # match in document order types into whichever node the form is
            # hiding. A contact's name is drawn twice, one hidden by the other,
            # so the box on screen stayed empty and Save was reported broken.
            " const host = [...document.querySelectorAll(%s)]"
            "   .find(el => el.getClientRects().length);"
            " if (!host) return 'missing';"
            # A line on an order is not a value in a box: typing into its first
            # cell leaves a half-made row, the form stays dirty, and Save is
            # then reported as broken when the walkthrough is fine.
            " if (host.closest('.o_field_x2many')) return 'skipped-lines';"
            " const el = host.matches('input,textarea,select') ? host"
            "   : host.querySelector('input:not([type=hidden]),textarea,select');"
            " if (!el) return 'no-control';"
            # A form that arrives with a value in the box has chosen one that
            # agrees with the rest of the record: a payment opens with a bank
            # journal and the payment method that belongs to it. Typing over
            # both put a method from one journal against another, and Odoo
            # refused the record — a combination no user would have made,
            # since they would have left what was already there.
            " if (el.value && el.type !== 'checkbox' && el.type !== 'radio')"
            "   return 'already-filled';"
            # A link to another record cannot be typed, only chosen.
            " if (host.closest('.o_field_many2one, .o_field_many2one_avatar')"
            "     || el.getAttribute('role') === 'combobox')"
            "   return 'needs-a-record';"
            " if (el.tagName === 'SELECT') {"
            "   const option = [...el.options].find(o => o.value);"
            "   if (!option) return 'no-option';"
            "   el.value = option.value;"
            " } else if (el.type === 'radio') {"
            # One of a group, and the form has already chosen the sensible one.
            # Clicking blindly flipped a payment from incoming to outgoing,
            # which left its payment method belonging to the other kind and
            # the record refusing to save — three walkthroughs reported broken
            # over a choice the instrument made and no user would.
            "   const group = el.name ? document.getElementsByName(el.name) : [el];"
            "   if ([...group].some(one => one.checked)) return 'already-chosen';"
            "   el.click();"
            "   return 'chosen';"
            " } else if (el.type === 'checkbox') {"
            "   if (!el.checked) el.click();"
            "   return 'checked';"
            " } else {"
            # What kind of box this is comes from Odoo's own wrapper, not from
            # the input's type: it draws dates and numbers as plain text
            # boxes, so the type says "text" for all of them and the generic
            # value went into every one. A date field holding "V1234567" is
            # invalid, the record will not save, and the walkthrough is then
            # reported broken at a Save step that was never the problem.
            "   const kind = c => el.closest(c) || host.closest(c);"
            "   if (kind('.o_field_datetime')) { el.value = '01/01/2026 10:00:00'; }"
            "   else if (kind('.o_field_date, .o_field_daterange'))"
            "     { el.value = '01/01/2026'; }"
            "   else if (kind('.o_field_float, .o_field_integer,"
            "     .o_field_monetary, .o_field_percentage, .o_field_float_time'))"
            "     { el.value = '1'; }"
            "   else if (el.type === 'date') { el.value = '2026-01-01'; }"
            "   else if (el.type === 'number' || el.inputMode === 'decimal')"
            "     { el.value = '1'; }"
            "   else if (el.type === 'email') { el.value = 'v@example.com'; }"
            "   else { el.value = 'V' + String(Date.now()).slice(-7); }"
            " }"
            " el.dispatchEvent(new Event('input', {bubbles: true}));"
            " el.dispatchEvent(new Event('change', {bubbles: true}));"
            " return 'filled'; })()"
        ) % json.dumps(selector)
        outcome = self.run("eval", script, check=False).strip().strip('"')
        if outcome == "needs-a-record":
            return self._pick_a_record(selector)
        return outcome

    def _pick_a_record(self, selector):
        """Choose an existing record from the dropdown, as a person would.

        Typing free text into a link field leaves a value matching no record,
        so the form refuses to save and every walkthrough that fills one is
        reported broken at its Save step. That was the whole of the purchase
        walkthrough's single fault, and the walkthrough was fine.
        """
        self.run("eval", (
            "(() => { const visible = sel => [...document.querySelectorAll(sel)]"
            "   .find(el => el.getClientRects().length);"
            " const i = visible(%s + ' input') || visible(%s);"
            " if (!i) return 'missing';"
            " i.focus(); i.value = 'a';"
            " i.dispatchEvent(new Event('input', {bubbles: true}));"
            " return 'typed'; })()"
        ) % (json.dumps(selector), json.dumps(selector)), check=False)
        time.sleep(2)
        outcome = self.run("eval", (
            "(() => {"
            " const existing = document.querySelector("
            "   '.o-autocomplete--dropdown-item:not(.o_m2o_dropdown_option) a');"
            " if (existing) { existing.click(); return 'picked'; }"
            # Nothing to pick is not a dead end for a person: the dropdown
            # offers to create one, and they take it. Maintenance equipment is
            # empty on this database, so refusing that option measured the
            # walkthrough as broken where a human would have carried on.
            " const make = [...document.querySelectorAll("
            "   '.o_m2o_dropdown_option a, .o-autocomplete--dropdown-item a')]"
            "   .find(a => /create|إنشاء/i.test(a.textContent));"
            " if (!make) return 'no-options';"
            " make.click(); return 'created'; })()"
        ), check=False).strip().strip('"')
        if outcome == "created":
            # Quick-create may open a dialog for the rest of the record.
            time.sleep(2)
            self.run("eval", (
                "(() => { const save = document.querySelector("
                "   '.modal-footer .btn-primary');"
                " if (save) { save.click(); return 'saved'; }"
                " return 'no-dialog'; })()"
            ), check=False)
            time.sleep(2)
        return outcome

    def saved(self):
        """Whether the form in front of us is a record that actually saved.

        Waited for rather than read at once. Saving is a round trip, and the
        unsaved indicator is still on screen while it happens — so reading
        immediately after the click called a dozen good walkthroughs broken at
        their Save step, each of which saved perfectly when walked by hand.
        """
        for _ in range(8):
            state = self._save_state()
            if state != "unsaved":
                return state
            time.sleep(1)
        return self._save_state()

    def _save_state(self):
        script = (
            "(() => {"
            " if (document.querySelector('.o_form_status_indicator_buttons:not(.invisible)'))"
            "   return 'unsaved';"
            " if (document.querySelector('.o_field_invalid, .o_notification.border-danger'))"
            "   return 'refused';"
            " return document.querySelector('.o_form_view') ? 'saved' : 'no-form';"
            " })()"
        )
        return self.run("eval", script, check=False).strip().strip('"')

    def click(self, selector):
        """Follow the step, and make sure it was really followed.

        A click that quietly does nothing is the worst outcome here. Every
        later step is then checked against a page the walkthrough never
        reached, so one lost click reports a working walkthrough as broken
        from that point down — which is exactly what happened: the same tour
        came back with three broken steps on one run and four on the next,
        against an unchanged database.

        Two causes, both fixed here. The element can be detached between being
        looked at and being clicked, since the page is still settling from the
        step before; that click fails and was being swallowed. And a click that
        opens a form needs the form to arrive before the next trigger is looked
        for — waiting only on the trigger is not enough when the trigger is
        still on the outgoing page.

        Three attempts rather than two, and the page is allowed to settle
        before the first one. The apps home screen renders, then renders again
        when its list arrives, so an element checked a moment earlier can be
        detached by the time the click is aimed at it. That cost an evening:
        the purchase walkthrough was reported broken from step 2 down over a
        lost click on the app tile, and walking it by hand worked every time.
        """
        self.run("wait", "--load", "networkidle", check=False)
        for attempt in (1, 2, 3):
            try:
                self.run("click", selector)
                break
            except RuntimeError:
                self.await_element(selector)
                if attempt < 3:
                    continue
                # Last resort: click it from the page itself. The browser
                # driver resolves a selector through its own view of the
                # document and would not take the app tile on the home screen,
                # while document.querySelector found it every time. A step is
                # not broken because the instrument could not aim at it.
                landed = self.run(
                    "eval",
                    "(() => { const el = document.querySelector(%s);"
                    " if (!el) return 'no'; el.click(); return 'yes'; })()"
                    % json.dumps(selector),
                    check=False,
                )
                if "yes" not in landed:
                    return False
        self.run("wait", "--load", "networkidle", check=False)
        return True

    def start_clean(self, address):
        """Open the page, and make sure we really left the last walkthrough.

        A walk that ends inside a form with unsaved changes leaves Odoo holding
        the page: navigating away raises the discard prompt, the navigation
        does not happen, and the next walkthrough is then measured against the
        previous one's screen. Every walkthrough after such a one was reported
        broken at its first step — three of eight in a sample, none of them
        actually broken.
        """
        for attempt in range(3):
            self.open(address)
            time.sleep(2)
            if self.run("eval", "!!document.querySelector('.o_home_menu')",
                        check=False).strip() == "true":
                return True
            # Whatever is holding the page: a discard prompt, or a form that
            # has not been told to let go.
            self.run("eval", (
                "(() => {"
                " const discard = document.querySelector("
                "   '.modal-footer .btn-primary, .o_form_button_cancel');"
                " if (discard) { discard.click(); return 'discarded'; }"
                " return 'nothing-to-discard'; })()"
            ), check=False)
            time.sleep(1)
        return False

    def dismiss(self):
        """Close whatever the last step opened over the page.

        Clicking a date field opens its picker, and the picker sits over the
        form until something closes it — so the next click lands on the picker
        rather than on Save, the record stays unsaved, and the walkthrough is
        reported broken at a step that was never reached. A person presses
        escape without thinking about it.
        """
        # Blur, and nothing else. Escape looked like the natural way to close
        # a picker until it was read as what Odoo means by it — discard the
        # changes on this form — which quietly emptied the record between two
        # steps and left Save with nothing to do. Leaving the field is enough:
        # the picker closes with the focus that opened it.
        self.run("eval", (
            "(() => { const open = document.activeElement;"
            " if (open && open.blur) open.blur();"
            " const away = document.querySelector('.o_form_sheet, .o_content');"
            " if (away) away.dispatchEvent(new MouseEvent('mousedown',"
            "   {bubbles: true}));"
            " return 'dismissed'; })()"
        ), check=False)

    def scalar(self, expression):
        """One value out of the page, unwrapped from the driver's quoting."""
        return self.run("eval", expression, check=False).strip().strip('"')

    def begin_tour(self, name):
        """Start a walkthrough the way the module does, and let it drive.

        Telling the server first is not a formality. A walkthrough of several
        stages only hands over because the server is holding a record of one in
        progress; a tour started behind its back walks its first stage and
        stops, with nothing anywhere saying why. That is exactly what a user
        met, and a measurement that skips this call measures a path nobody
        takes.
        """
        self.run("eval", (
            "['current_tour','current_tour.index',"
            " 'era_web_tour_assistant.resumed_stage',"
            " 'era_web_tour_assistant.walked_stage']"
            ".forEach(k => localStorage.removeItem(k)); 1"
        ), check=False)
        self.run("eval", (
            "(async () => { const answer = await fetch('/web/dataset/call_kw',"
            " {method: 'POST', headers: {'Content-Type': 'application/json'},"
            "  body: JSON.stringify({jsonrpc: '2.0', method: 'call', params:"
            "   {model: 'web_tour.tour', method: 'assistant_begin',"
            "    args: [%s], kwargs: {}}})}).then(r => r.json());"
            " window.__began = !!(answer && answer.result); return 'told'; })()"
            % json.dumps(name)
        ), check=False)
        time.sleep(2)
        self.run("eval", (
            "odoo.startTour(%s, {mode:'manual', redirect:false, fromDB:true}); 1"
            % json.dumps(name)
        ), check=False)
        time.sleep(3)

    def running(self):
        """Which stage the engine thinks it is on, and how far into it."""
        name = self.scalar("localStorage.getItem('current_tour')")
        raw = self.scalar("localStorage.getItem('current_tour.index')")
        try:
            index = int(raw)
        except (TypeError, ValueError):
            index = 0
        return (None if name in ("null", "", "undefined") else name), index

    def close(self):
        self.run("close", check=False)


def odoo_call(url, db, uid, password, model, method, *args):
    payload = {
        "jsonrpc": "2.0", "method": "call",
        "params": {
            "service": "object", "method": "execute_kw",
            "args": [db, uid, password, model, method, list(args)],
        },
        "id": 1,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/jsonrpc", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.loads(response.read())
    if "error" in body:
        raise RuntimeError(json.dumps(body["error"])[:400])
    return body["result"]


def login(url, db, user, password):
    payload = {
        "jsonrpc": "2.0", "method": "call",
        "params": {"service": "common", "method": "login",
                   "args": [db, user, password]},
        "id": 1,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/jsonrpc", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        uid = json.loads(response.read()).get("result")
    if not uid:
        raise SystemExit("Odoo rejected those credentials.")
    return uid


def verify(page, url, stages, fill=False):
    """Walk a whole walkthrough, returning the steps that were not there.

    Each step is clicked before the next is checked, because most of them only
    become reachable once the one before has been followed — which is the whole
    point of a walkthrough, and the reason checking the triggers all at once
    against one page would report almost every step as missing.

    A walkthrough of several screens is a chain of tours, and the page is
    opened once for the whole chain rather than once per stage. A later stage
    begins where the one before it ended — its first step leaves the app it is
    standing in — so starting it from the home screen reports a step as hidden
    that no user will ever meet there. That is what this script said the first
    time it was pointed at a chain.
    """
    page.start_clean(url.rstrip("/") + (stages[0]["url"] or "/odoo"))
    problems, index = [], 0
    for stage in stages:
        for step in stage["steps"]:
            index += 1
            page.await_element(step["trigger"])
            state = page.visible(step["trigger"])
            if state != "ok":
                # Where the browser actually was. "missing" on its own sends
                # you looking for a fault in the step, when the page may be
                # somewhere else entirely -- a login screen, or the app before
                # the one the step belongs to.
                problems.append((index, step["trigger"], state, step["content"],
                                 page.where()))
                # Carry on rather than stop: one unreachable step early on
                # would otherwise hide every later one, and the report is more
                # useful naming all of them.
                continue
            if fill and '[name=' in step["trigger"]:
                page.fill(step["trigger"])
                filled_a_field = True
            else:
                filled_a_field = False
            if fill and step["trigger"] == ".o_form_button_save":
                page.click(step["trigger"])
                state = page.saved()
                if state != "saved":
                    problems.append((index, step["trigger"], "not " + state,
                                     step["content"], page.where()))
                continue
            if filled_a_field:
                # Filling it is following it. The click that used to come after
                # opened whatever the widget opens — a date picker over the
                # next step, a link dropdown that swallowed the one after — and
                # on a payment it reached the radio for the payment type and
                # turned an incoming payment into an outgoing one, so the
                # method no longer matched the journal and the record refused
                # to save. Three walkthroughs were reported broken over a
                # gesture no user makes.
                page.dismiss()
                continue
            if not page.click(step["trigger"]):
                # A step that was there and would not take the click. Every
                # later step would now be measured against a page the walk
                # never reached, so stopping is the only honest thing: this
                # walkthrough was reported broken from step 2 down for an
                # evening, on the strength of one click that never landed and
                # a report that carried on regardless.
                problems.append((index, step["trigger"], "click lost",
                                 step["content"], page.where()))
                return problems
    return problems


def follow_the_engine(page, url, stages, fill=False):
    """Walk a walkthrough by letting the tour engine walk it.

    The difference from verify(): that one reads the steps out of the database
    and clicks them itself, so the engine is never asked to do anything. It
    measures that every step names something on the screen — which is true and
    is not the same as the walkthrough working.

    What it cannot see is the seam. web_tour ends a stage, calls consume(), and
    the next stage has to start; that handover is where this module's own code
    runs, and it is where the walkthrough broke in front of a user while the
    verifier reported sixty of sixty-five clean. So here the engine is started
    and then obeyed: click what the current step points at, and check the
    engine moved on. A stage that ends must be followed by the next one
    arriving on its own.

    Slower than verify() by the wait after every step, so it is pointed at
    chains — a walkthrough of one stage has no handover to lose.
    """
    problems = []
    page.start_clean(url.rstrip("/") + (stages[0]["url"] or "/odoo"))
    page.begin_tour(stages[0]["name"])

    running, index = page.running()
    if running != stages[0]["name"]:
        return [(0, stages[0]["name"], "never started", "", page.where())]

    for position, stage in enumerate(stages):
        steps = stage["steps"]
        for number, step in enumerate(steps):
            running, index = page.running()
            if running != stage["name"]:
                problems.append((number, step["trigger"], "stage lost",
                                 step.get("content") or "", page.where()))
                return problems
            if fill and '[name=' in step["trigger"]:
                page.fill(step["trigger"])
                filled_a_field = True
            else:
                filled_a_field = False
            page.click(step["trigger"])
            if filled_a_field:
                page.dismiss()
            time.sleep(1.5)

            moved, moved_index = page.running()
            last = number == len(steps) - 1
            if not last and moved == running and moved_index <= index:
                # The step was there and was clicked, and the engine did not
                # count it. That is a step the user can see and cannot pass.
                problems.append((number, step["trigger"], "engine stuck",
                                 step.get("content") or "", page.where()))
                return problems

        if position + 1 < len(stages):
            # The handover. Nothing here clicks anything: if the next stage
            # does not arrive by itself, the walkthrough has ended early and
            # the user is left looking at a screen with no pointer on it.
            following = stages[position + 1]["name"]
            for _ in range(10):
                time.sleep(1)
                arrived, _index = page.running()
                if arrived == following:
                    break
            else:
                problems.append((0, following, "stage never arrived",
                                 "handover after %s" % stage["name"],
                                 page.where()))
                return problems
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0,
                        help="Check only the most recent N tours.")
    parser.add_argument("--fill", action="store_true",
                        help="Type into the fields and check the record really "
                             "saves. Measures whether the task can be finished "
                             "rather than whether the steps appear — and it "
                             "creates records, so scratch only.")
    parser.add_argument("--engine", action="store_true",
                        help="Let the tour engine drive, and check it advances "
                             "and hands over between stages. Slower, and only "
                             "chains are walked — a single stage has no "
                             "handover to lose.")
    parser.add_argument("--only", default="",
                        help="Check one walkthrough by name, in a browser of "
                             "its own. A walk that fails in a full run and "
                             "passes alone is a fault of the run, not the "
                             "walkthrough.")
    args = parser.parse_args()

    required = ["SCRATCH_URL", "SCRATCH_DB", "SCRATCH_LOGIN", "SCRATCH_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit("missing environment: %s" % ", ".join(missing))
    url = os.environ["SCRATCH_URL"]
    db = os.environ["SCRATCH_DB"]
    password = os.environ["SCRATCH_PASSWORD"]
    uid = login(url, db, os.environ["SCRATCH_LOGIN"], password)

    ids = odoo_call(url, db, uid, password, "web_tour.tour", "search",
                    [("assistant_generated", "=", True)])
    every = odoo_call(url, db, uid, password, "web_tour.tour", "read", ids,
                      ["name", "url", "step_ids", "assistant_next_stage_id",
                       "assistant_first_stage_id"])

    # A walkthrough is the chain, not the tour: only heads are walked, and each
    # carries the stages that follow it.
    by_id = {record["id"]: record for record in every}
    records = []
    for record in every:
        if record["assistant_first_stage_id"]:
            continue
        chain, following = [record], record["assistant_next_stage_id"]
        while following and following[0] in by_id:
            chain.append(by_id[following[0]])
            following = chain[-1]["assistant_next_stage_id"]
        records.append(chain)
    if args.only:
        records = [chain for chain in records
                   if any(record["name"] == args.only for record in chain)]
        if not records:
            raise SystemExit("no walkthrough named %s" % args.only)
    if args.engine:
        # A single stage has no handover, which is the whole point of this
        # mode. Said out loud rather than silently: a run that reports "8 of 8
        # fine" while skipping most of the library is worse than no run.
        chains = [chain for chain in records if len(chain) > 1]
        print("%d of %d walkthroughs cross more than one screen; walking those."
              % (len(chains), len(records)))
        records = chains
    if args.limit:
        records = records[-args.limit:]

    page = Page()
    page.login(url, os.environ["SCRATCH_LOGIN"], password)
    bad = 0
    try:
        for chain in records:
            stages, total = [], 0
            for record in chain:
                steps = odoo_call(url, db, uid, password, "web_tour.tour.step",
                                  "read", record["step_ids"],
                                  ["trigger", "content"])
                stages.append({"url": record["url"], "steps": steps,
                               "name": record["name"]})
                total += len(steps)
            walk = follow_the_engine if args.engine else verify
            problems = walk(page, url, stages, fill=args.fill)
            shape = "%d steps" % total
            if len(chain) > 1:
                shape += " over %d stages" % len(chain)
            if not problems:
                print("ok    %s (%s)" % (chain[0]["name"], shape))
                continue
            bad += 1
            print("BROKEN %s (%s)" % (chain[0]["name"], shape))
            for index, trigger, state, content, where in problems:
                print("   step %d %-8s %s" % (index, state, trigger))
                print("            %s" % (content or "")[:70])
                print("            page was: %s" % where)
    finally:
        page.close()

    print("\n%d of %d walkthroughs have a step the user cannot see."
          % (bad, len(records)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

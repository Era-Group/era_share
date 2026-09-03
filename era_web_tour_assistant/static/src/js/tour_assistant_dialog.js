import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

export class TourAssistantDialog extends Component {
    static template = "era_web_tour_assistant.AskDialog";
    static components = { Dialog };
    static props = {
        close: { type: Function },
    };

    setup() {
        this.orm = useService("orm");
        this.tourService = useService("tour_service");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.questionRef = useRef("question");
        this.state = useState({
            question: "",
            busy: false,
            message: "",
            wait: "",
            library: [],
            sift: "",
        });
        onMounted(() => {
            this.questionRef.el?.focus();
            this.loadEstimate();
            this.loadLibrary();
        });
    }

    /**
     * How long working an answer out has taken on this database, fetched
     * while the user is still typing so the wait can be named the moment they
     * press the button rather than a round trip later. It stays quiet until
     * enough builds are on record: a made-up number is worse than none.
     */
    async loadEstimate() {
        let middle, upper;
        try {
            [middle, upper] = await this.orm.call(
                "tour.assistant.request", "build_estimate", []
            );
        } catch {
            return;  // never let a nicety stop somebody asking a question
        }
        if (!upper) {
            return;
        }
        this.state.wait = _t(
            "Usually about %(middle)s seconds, rarely more than %(upper)s.",
            { middle: Math.round(middle), upper: Math.round(upper) }
        );
    }

    /**
     * The walkthroughs already worked out that this user may run.
     *
     * Running one again meant typing the question again and waiting to be
     * matched, or going into Settings — which is not a place a warehouse
     * clerk goes. The server decides what belongs here: the same gate that
     * governs whether a question may be answered with a walkthrough governs
     * whether it is listed.
     */
    async loadLibrary() {
        try {
            const rows = await this.orm.call(
                "web_tour.tour", "assistant_library", []);
            this.state.library = rows.map((walk) => ({
                ...walk,
                stepsLabel: _t("%(count)s steps", { count: walk.steps }),
                stagesLabel: _t("%(count)s screens", { count: walk.stages }),
            }));
        } catch {
            this.state.library = [];  // a convenience must never block asking
        }
    }

    /**
     * The rows to draw: all of them, or the ones the box above matches.
     *
     * Matched on what somebody would type — the words of the question they or
     * a colleague asked — and normalised only for case and edges, since the
     * labels are the askers' own wording rather than anything this module
     * chose. A hundred and fifty rows is a list you scroll past; the same
     * hundred and fifty with two words typed is a list you use.
     */
    get shown() {
        const sift = this.state.sift.trim().toLowerCase();
        if (!sift) {
            return this.state.library;
        }
        return this.state.library.filter(
            (walk) => (walk.label || "").toLowerCase().includes(sift));
    }

    /**
     * Start one from the list. Closing first, for the reason submit() does:
     * the dialog otherwise covers the very step being pointed at.
     */
    async replay(walk) {
        // Told before the dialog closes, not after. Closing destroys this
        // component, and an await that resolves into a destroyed component
        // never reaches the line below it — which turned "the walkthrough
        // stops at stage one" into "the button does nothing at all".
        //
        // The server has to know a walkthrough is running, or the handover at
        // the end of each stage has no mark to move and the walkthrough stops
        // where its first stage does. Asking a question records that; running
        // one again from this list did not.
        try {
            await this.orm.call("web_tour.tour", "assistant_begin", [walk.name]);
        } catch {
            // Worth starting anyway: a walkthrough of one stage needs none of
            // this, and refusing to run would be the worse failure.
        }
        this.props.close();
        await this.startTour(walk);
    }

    /**
     * Say a walkthrough did not help. One click and no form: a sentence typed
     * in frustration is worth less than the fact itself, and asking for one is
     * how nobody reports anything. Views differ between databases, so the
     * people using it are the only instrument that covers them all.
     */
    async report(walk) {
        // Asked first. A report is a measurement — it is how a fault on a
        // database nobody here can see gets found — and one sent by a stray
        // click is a measurement of nothing that costs somebody an
        // investigation. One dialog is cheap against that.
        this.dialog.add(ConfirmationDialog, {
            title: _t("Report this walkthrough?"),
            body: _t(
                "You are about to tell us that “%(label)s” did not help. It " +
                "will be looked at and rebuilt. Nothing you typed is sent.",
                { label: walk.label }
            ),
            confirmLabel: _t("Yes, it did not help"),
            cancelLabel: _t("Cancel"),
            confirm: async () => {
                walk.reported = true;
                try {
                    await this.orm.call(
                        "tour.assistant.request", "report_unhelpful",
                        [walk.name]);
                    this.notification.add(
                        _t("Noted — thank you. Somebody will look at this one."),
                        { type: "success" }
                    );
                } catch {
                    walk.reported = false;
                }
            },
            cancel: () => {},
        });
    }

    get title() {
        // Translated here rather than in the template: a prop is a JS
        // expression, so a literal written there is never picked up.
        return _t("Show me how");
    }

    get canSubmit() {
        return !this.state.busy && this.state.question.trim().length > 1;
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.submit();
        }
    }

    async submit() {
        if (!this.canSubmit) {
            return;
        }
        const question = this.state.question.trim();
        this.state.busy = true;
        this.state.message = "";
        let result;
        try {
            result = await this.orm.call("tour.assistant.request", "ask", [question]);
        } finally {
            this.state.busy = false;
        }

        if (result.state === "matched") {
            // Closing first, otherwise the dialog covers the very first step
            // the pointer is trying to point at.
            this.props.close();
            await this.startTour(result.tour);
            return;
        }
        this.state.message = result.message;
    }

    async startTour(tour) {
        // A fresh run of a walkthrough clears the marks the last one left.
        // Each later stage is resumed once — which is what stops a walkthrough
        // somebody closed from reopening a second later — and "once" has to
        // mean once per run, not once per browser. Without this the second
        // time anybody walks the same multi-stage walkthrough it stops at the
        // end of its first stage and nothing says why.
        browser.localStorage.removeItem("era_web_tour_assistant.resumed_stage");
        browser.localStorage.removeItem("era_web_tour_assistant.walked_stage");
        await this.backToTheApps();
        try {
            await this.tourService.startTour(tour.name, {
                mode: "manual",
                fromDB: true,
                url: tour.url,
            });
        } catch (error) {
            this.notification.add(
                _t("This walkthrough could not be started. Someone will need to look at it."),
                { type: "danger" }
            );
            throw error;
        }
    }

    /**
     * Leave whatever app we are in, so the first step has something to hit.
     *
     * A walkthrough starts by clicking the app it needs, and inside an app the
     * navbar draws that app's own menus and no other app's root — measured:
     * from Inventory, `[data-menu-xmlid="purchase.menu_purchase_root"]` is not
     * in the page at all. The pointer then waits on an element that is never
     * coming, which looks exactly like the assistant doing nothing.
     *
     * It matters because of where people ask from. Nobody goes to the home
     * screen to ask how to do something — they ask while stuck in the middle
     * of the app they are stuck in, which is the one case this failed.
     *
     * The same click the walkthrough itself uses between stages, waited on
     * rather than assumed: the app switcher renders on the next frame, and
     * starting the tour before it does puts us back where we started.
     */
    async backToTheApps() {
        if (document.querySelector(".o_home_menu")) {
            return;
        }
        const toggle = document.querySelector(".o_menu_toggle");
        if (!toggle) {
            return;
        }
        toggle.click();
        for (let waited = 0; waited < 20; waited++) {
            await new Promise((resolve) => browser.setTimeout(resolve, 100));
            if (document.querySelector(".o_home_menu")) {
                return;
            }
        }
    }
}

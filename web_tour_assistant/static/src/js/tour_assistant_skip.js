import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { tourState } from "@web_tour/js/tour_state";

/**
 * Carry a user past a stage they have already done.
 *
 * A walkthrough of several screens is a chain of tours, one per stage, and a
 * stage that exists to create something is wasted on somebody who opens an
 * existing record instead. The interactive engine cannot express that: it
 * walks a tour's steps in order with no way to leave one out, so the only way
 * past used to be stopping the whole walkthrough.
 *
 * This watches what the user actually opened. When the screen shows a saved
 * record of the model the stage was going to create, the server is asked for
 * the next stage and it starts — no button, no question.
 *
 * The one thing it must not do is fire at the *end* of a stage somebody did
 * walk: saving a new record leaves exactly the same screen. So a stage that
 * has been seen in creation at any point is never skipped again — the user
 * took the long way, and the completion belongs to them.
 *
 * That mark is kept in localStorage rather than in memory, for the reason
 * web_tour keeps its own state there: a tour survives a page reload, so
 * anything guarding it has to survive one too. Held in memory it was lost on
 * reload and the walkthrough then skipped the stage the user had just done —
 * seen happening in a browser, which is the only way it was ever going to be.
 */

const POLL_MS = 700;
const WALKED = "web_tour_assistant.walked_stage";
const RESUMED = "web_tour_assistant.resumed_stage";

export const tourAssistantSkipService = {
    dependencies: ["action", "orm", "tour_service"],

    start(env, { action, orm, tour_service }) {
        let watching = null;   // name of the stage being watched
        let skipModel = null;  // model whose saved record ends it early

        async function look() {
            const running = tourState.getCurrentTour();
            if (!running) {
                watching = null;
                skipModel = null;
                await resume();
                return;
            }

            if (running !== watching) {
                // A new stage: ask what, if anything, ends it early. Stages
                // arrive from consume() without passing through our code, so
                // there is no other moment to learn this.
                watching = running;
                skipModel = null;
                try {
                    const info = await orm.call(
                        "web_tour.tour", "assistant_stage_info", [running]);
                    skipModel = (info && info.skip_model) || null;
                } catch {
                    skipModel = null;  // a walkthrough must not depend on this
                }
            }

            if (!skipModel || browser.localStorage.getItem(WALKED) === running) {
                return;
            }

            const props = action.currentController?.props;
            if (!props || props.resModel !== skipModel) {
                return;
            }

            if (!props.resId) {
                // An unsaved record: the user pressed New and is being walked
                // through it. From here this stage is theirs to finish.
                browser.localStorage.setItem(WALKED, running);
                return;
            }

            let next;
            try {
                next = await orm.call(
                    "web_tour.tour", "assistant_skip_stage", [running]);
            } catch {
                return;
            }
            if (next && next.name) {
                browser.localStorage.setItem(RESUMED, next.name);
                tour_service.startTour(next.name, {
                    mode: "manual",
                    redirect: false,
                    fromDB: true,
                    url: next.url,
                });
            }
        }

        /**
         * Start the stage the user is part-way through, when nothing is
         * running.
         *
         * web_tour hands the next stage to startTour without `fromDB`, and
         * startTour drops any tour that is not in the javascript registry --
         * which is every tour this module generates. So the walkthrough ended
         * silently at the end of its first stage. Proved in a browser: the
         * same call starts the tour with `fromDB` and does nothing without it.
         *
         * Resumed once per stage. A user who closes a walkthrough has closed
         * it; the pending flag is only cleared when a stage finishes, so
         * without this the tour would start again every second.
         */
        async function resume() {
            let stage;
            try {
                stage = await orm.call("web_tour.tour", "assistant_pending_stage", []);
            } catch {
                return;
            }
            if (!stage || !stage.name) {
                return;
            }
            if (browser.localStorage.getItem(RESUMED) === stage.name) {
                return;
            }
            browser.localStorage.setItem(RESUMED, stage.name);
            tour_service.startTour(stage.name, {
                mode: "manual",
                redirect: false,
                fromDB: true,
                url: stage.url,
            });
        }

        setInterval(() => look(), POLL_MS);
        return {};
    },
};

registry.category("services").add("tour_assistant_skip", tourAssistantSkipService);

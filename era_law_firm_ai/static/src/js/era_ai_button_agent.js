/**
 * One AI button, and the agent behind it follows where you are.
 *
 * Odoo's button is the only one on screen — a second button for the same
 * gesture only makes a lawyer wonder which one they should have pressed. What
 * changes is who answers it:
 *
 *   anywhere in this app   → the legal advisor: the statute corpus, and the
 *                            open record with its case file when there is one
 *   anywhere else in Odoo  → untouched, Odoo's own assistant
 *
 * It is the app that decides, not the model: the same lawyer asks about a
 * statute, a case and the dashboard in one sitting, and a rule written model
 * by model would answer in a different voice on whichever screen nobody
 * thought to list. One of our records reached from outside the app is still
 * answered by the advisor — that is the composer on Odoo's own key.
 *
 * Which agent each key opens is a data decision: the two ai.composer rows.
 * This file only says which key applies where.
 */
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import SystrayAction from "@ai/web/systray_action";
import { useInLawFirmApp } from "@era_law_firm_ai/js/era_app_watch";

const LEGAL_KEY = "era_legal_research";

patch(SystrayAction.prototype, {
    setup() {
        super.setup();
        this.eraInLawFirm = useInLawFirmApp();
    },

    get eraAiTitle() {
        // Odoo's own label is set again here rather than left in place: the
        // template can hold one title, and a dynamic one replaces the static
        // one wherever it is bound.
        return this.eraInLawFirm.value ? _t("Legal Advisor") : _t("Ask AI");
    },

    async onClickLaunchAIChat() {
        if (!this.eraInLawFirm.value) {
            return super.onClickLaunchAIChat();
        }
        if (this.actionService.currentController?.view?.type === "form") {
            // Odoo's own route for a form, so the open record travels with the
            // question — the form controller saves it and sends it along.
            // Only the key it announces changes, and with it the agent.
            this.env.bus.trigger("AI:OPEN_AI_CHAT", { origin: LEGAL_KEY });
            return;
        }
        this.aiChatLauncher.launchAIChat({ callerComponentName: LEGAL_KEY });
    },
});

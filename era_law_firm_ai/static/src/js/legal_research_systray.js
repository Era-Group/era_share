/**
 * The firm's own AI button in the navbar, shown only inside the law firm app.
 *
 * Odoo's own AI button stays exactly as it is, next to this one: it belongs to
 * the whole database and its agent has no legal sources, which is right for
 * every other app and wrong for a legal question. The agent behind this button
 * has both halves — the statute corpus, and the record the lawyer has open.
 *
 * On a form the click goes through the form controller, because that is what
 * holds the record; anywhere else it opens on the corpus alone.
 *
 * The agent is not named here. The button sends an interface key and the server
 * looks up the ai.composer record that answers for it, which is where the agent,
 * the standing instructions and the starter prompts live — so a firm can point
 * the button elsewhere without a deployment.
 */
import { Component } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useInLawFirmApp } from "@era_law_firm_ai/js/era_app_watch";

const RESEARCH_KEY = "era_legal_research";

export class LegalResearchSystray extends Component {
    static props = {};
    static template = "era_law_firm_ai.LegalResearchSystray";

    setup() {
        this.actionService = useService("action");
        this.aiChatLauncher = useService("aiChatLauncher");
        this.label = _t("Legal Advisor");
        this.inLawFirm = useInLawFirmApp();
    }

    onClickResearch() {
        // On a record, the file travels with the question. The form controller
        // is what holds the record, so the request goes through it the way
        // Odoo's own button does — and the server fills in the file's hearings,
        // deadlines and documents from the record itself.
        if (this.actionService.currentController?.view?.type === "form") {
            this.env.bus.trigger("AI:OPEN_AI_CHAT", { origin: RESEARCH_KEY });
            return;
        }
        this.aiChatLauncher.launchAIChat({
            callerComponentName: RESEARCH_KEY,
            channelTitle: this.label,
        });
    }
}

registry
    .category("systray")
    .add(
        "era_law_firm_ai.legal_research",
        { Component: LegalResearchSystray },
        // just after Odoo's own AI button, so the two read as a pair
        { sequence: 31 }
    );

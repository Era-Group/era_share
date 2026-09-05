/**
 * A statutory-research button in the navbar, shown only inside the law firm app.
 *
 * Odoo's own AI button stays exactly as it is: it belongs to the whole database,
 * and the agent behind this one answers only from the indexed statutes, so
 * taking the general assistant over would have made both worse.
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
        this.aiChatLauncher = useService("aiChatLauncher");
        this.label = _t("Legal Research");
        this.inLawFirm = useInLawFirmApp();
    }

    onClickResearch() {
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

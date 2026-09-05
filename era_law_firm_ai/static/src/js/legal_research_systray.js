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
import { Component, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";

const LAW_FIRM_APP = "era_law_firm.menu_legal_root";
const RESEARCH_KEY = "era_legal_research";

export class LegalResearchSystray extends Component {
    static props = {};
    static template = "era_law_firm_ai.LegalResearchSystray";

    setup() {
        this.menuService = useService("menu");
        this.aiChatLauncher = useService("aiChatLauncher");
        this.label = _t("Legal Research");
        // Systray items are built once and live above the action, so the app
        // has to be watched rather than read: without this the button would
        // show whichever app happened to be open when the page loaded.
        this.state = useState({ inLawFirm: this.isInLawFirm() });
        useBus(this.env.bus, "MENUS:APP-CHANGED", () => {
            this.state.inLawFirm = this.isInLawFirm();
        });
    }

    isInLawFirm() {
        return this.menuService.getCurrentApp()?.xmlid === LAW_FIRM_APP;
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

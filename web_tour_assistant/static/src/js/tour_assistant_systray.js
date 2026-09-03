import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

import { TourAssistantDialog } from "./tour_assistant_dialog";

export class TourAssistantSystray extends Component {
    static template = "web_tour_assistant.Systray";
    static props = {};

    setup() {
        this.dialog = useService("dialog");
    }

    onClick() {
        this.dialog.add(TourAssistantDialog);
    }
}

registry.category("systray").add(
    "web_tour_assistant.ask",
    { Component: TourAssistantSystray },
    { sequence: 40 }
);

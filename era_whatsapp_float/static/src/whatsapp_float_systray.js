/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Small systray button (top navbar) that toggles the floating WhatsApp window,
 * complementing the always-visible bottom bubble.
 */
export class WhatsappFloatSystray extends Component {
    static template = "era_whatsapp_float.Systray";
    static props = {};

    setup() {
        this.float = useService("era_whatsapp_float");
        this.state = useState(this.float.state);
    }

    onClick() {
        this.float.toggle();
    }
}

registry.category("systray").add(
    "era_whatsapp_float.systray",
    { Component: WhatsappFloatSystray },
    { sequence: 25 }
);

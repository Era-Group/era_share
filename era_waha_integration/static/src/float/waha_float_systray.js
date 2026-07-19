/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Systray button (top navbar) that toggles the floating WhatsApp/WAHA window.
 */
export class WahaFloatSystray extends Component {
    static template = "era_waha_integration.FloatSystray";
    static props = {};

    setup() {
        this.float = useService("era_waha_float");
        this.state = useState(this.float.state);
    }

    onClick() {
        this.float.toggle();
    }
}

registry.category("systray").add(
    "era_waha_integration.float_systray",
    { Component: WahaFloatSystray },
    { sequence: 25 }
);

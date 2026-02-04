/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("actions").add("eraspy_console_log", (env, action) => {
    const payload = action?.params?.payload;
    const responses = action?.params?.responses;
    if (payload) {
        console.log("[EraSpy] Request payload:", payload);
    } else {
        console.log("[EraSpy] No request payload provided.");
    }
    if (responses) {
        console.log("[EraSpy] Response payload:", responses);
    }
    return { type: "ir.actions.act_window_close" };
});

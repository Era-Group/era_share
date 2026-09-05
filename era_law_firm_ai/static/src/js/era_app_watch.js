/**
 * "Am I inside the law firm app?", answered as it changes.
 *
 * The AI button in the systray needs it: which agent it opens depends on
 * whether the lawyer is standing in this app. The systray is built once and
 * outlives every action, so the app has to be watched rather than read — a
 * component that read it at startup would keep whatever answer happened to be
 * true when the page loaded.
 */
import { useState, useEnv } from "@odoo/owl";
import { useBus, useService } from "@web/core/utils/hooks";

export const LAW_FIRM_APP = "era_law_firm.menu_legal_root";

export function useInLawFirmApp() {
    const env = useEnv();
    const menuService = useService("menu");
    const read = () => menuService.getCurrentApp()?.xmlid === LAW_FIRM_APP;
    const state = useState({ value: read() });
    useBus(env.bus, "MENUS:APP-CHANGED", () => {
        state.value = read();
    });
    return state;
}

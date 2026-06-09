/** @odoo-module **/

import { ListController } from "@web/views/list/list_controller";
import { patch } from "@web/core/utils/patch";

patch(ListController.prototype, {
    /**
     * Intercept the "New" button on translation.session list views
     * and open the Quick Translate wizard popup instead.
     */
    async openNew(params = {}) {
        if (this.props.resModel === "translation.session") {
            await this.env.services.action.doAction({
                type: "ir.actions.act_window",
                name: "Quick Translate",
                res_model: "quick.translate.wizard",
                view_mode: "form",
                views: [[false, "form"]],
                target: "new",
            });
            return;
        }
        return super.openNew(params);
    },
});

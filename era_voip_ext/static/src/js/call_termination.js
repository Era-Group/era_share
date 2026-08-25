/** @odoo-module **/

import { CallService } from "@voip/core/call_service";
import { Session } from "@voip/core/session";
import { patch } from "@web/core/utils/patch";

const endRequests = new WeakMap();

patch(CallService.prototype, {
    end(call) {
        if (endRequests.has(call)) {
            return endRequests.get(call);
        }
        const request = super.end(...arguments).catch((error) => {
            endRequests.delete(call);
            throw error;
        });
        endRequests.set(call, request);
        return request;
    },
});

patch(Session.prototype, {
    _onSessionTerminated() {
        super._onSessionTerminated(...arguments);
        if (this.call.state === "ongoing") {
            this.callService.end(this.call);
        }
    },
});

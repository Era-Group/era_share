/** @odoo-module **/

import { registry } from "@web/core/registry";
import { onMounted, onWillUnmount } from "@odoo/owl";
import {
    ProgressBarField,
    progressBarField,
} from "@web/views/fields/progress_bar/progress_bar_field";

const ACTIVE_STATES = new Set([
    "queued",
    "transcribing",
    "extracting",
    "generating",
    "scope_analyzing",
]);

class BrdProgressField extends ProgressBarField {
    setup() {
        super.setup();
        this._timer = null;
        this._loading = false;
        onMounted(() => {
            this._timer = window.setInterval(() => this._poll(), 3000);
            this._poll();
        });
        onWillUnmount(() => {
            if (this._timer) {
                window.clearInterval(this._timer);
            }
        });
    }

    async _poll() {
        if (!ACTIVE_STATES.has(this.props.record.data.brd_state)) {
            if (this._timer) {
                window.clearInterval(this._timer);
                this._timer = null;
            }
            return;
        }
        if (this._loading) {
            return;
        }
        this._loading = true;
        try {
            if (await this.props.record.isDirty()) {
                return;
            }
            await this.props.record.load();
        } catch (_) {
            // A transient network/access error must not leave an unhandled
            // promise rejection or stop later polling attempts.
        } finally {
            this._loading = false;
        }
    }
}

registry.category("fields").add("brd_progressbar", {
    ...progressBarField,
    component: BrdProgressField,
});

/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { onMounted, onWillUnmount } from "@odoo/owl";

/**
 * Poll the translation.session record every 5 seconds while
 * is_translating is True.  When the background cron finishes it sets
 * is_translating = False and the interval is cleared.
 */
patch(FormController.prototype, {
    setup() {
        super.setup();

        if (this.props.resModel !== "translation.session") {
            return;
        }

        let pollTimer = null;

        const stopPoll = () => {
            if (pollTimer !== null) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
        };

        const startPoll = () => {
            if (pollTimer !== null) return;
            pollTimer = setInterval(async () => {
                try {
                    await this.model.root.load();
                    if (!this.model.root.data.is_translating) {
                        stopPoll();
                    }
                } catch (_e) {
                    // ignore transient network errors
                }
            }, 5000);
        };

        onMounted(() => {
            if (this.model?.root?.data?.is_translating) {
                startPoll();
            }
        });

        onWillUnmount(stopPoll);
    },
});

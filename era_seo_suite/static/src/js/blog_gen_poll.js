/** @odoo-module **/

/**
 * Field widget that polls the parent record every 3 seconds while its own
 * boolean value is `true`, calling `record.load()` to re-fetch from the
 * server. When the value flips to `false` the timer stops on its own.
 *
 * Wired in the era.seo.suite.hub form view as
 *
 *     <field name="is_article_pending"
 *            widget="era_auto_refresh_when_true"
 *            invisible="1"/>
 *
 * so the field is technically rendered (the widget needs a mount point)
 * but invisible to the user. The Generate Now button flips the underlying
 * `era_seo.article_pending` ICP to True, the cron clears it when done,
 * and the polling here re-reads the record so the "Recently generated"
 * table updates in place.
 */

import { registry } from "@web/core/registry";
import { CheckBoxField } from "@web/views/fields/boolean/boolean_field";
import { onMounted, onWillUnmount } from "@odoo/owl";

const POLL_MS = 3000;

class EraAutoRefreshWhenTrue extends CheckBoxField {
    setup() {
        super.setup();
        this._timer = null;
        onMounted(() => this._maybeStart());
        onWillUnmount(() => this._stop());
    }

    /** When the polled value already says "pending", arm the timer. */
    _maybeStart() {
        if (this._timer) return;
        if (!this.props.record?.data?.[this.props.name]) return;
        this._timer = window.setInterval(() => this._tick(), POLL_MS);
    }

    _stop() {
        if (this._timer) {
            window.clearInterval(this._timer);
            this._timer = null;
        }
    }

    async _tick() {
        try {
            await this.props.record.load();
        } catch (e) {
            // Don't let a transient error kill the polling — try again next tick.
            return;
        }
        if (!this.props.record?.data?.[this.props.name]) {
            this._stop();
        }
    }
}

EraAutoRefreshWhenTrue.template = "web.BooleanField";

registry.category("fields").add("era_auto_refresh_when_true", {
    component: EraAutoRefreshWhenTrue,
    supportedTypes: ["boolean"],
});

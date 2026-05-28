/** @odoo-module **/

/**
 * Field widget that polls `is_article_pending` directly via the ORM
 * service every 3 seconds. When the server-side flag flips to false the
 * polling stops and the form record is reloaded so the "Recently
 * generated" list, the spinner banner, and the Generate Now button
 * pick up the new state.
 *
 * Why a direct ORM read instead of `record.load()`: in Odoo 19 the
 * relational model caches the record's fields per request — calling
 * `record.load()` in a tight loop didn't reliably re-fetch on every
 * tick, so the spinner stayed up after the cron had already cleared
 * the flag. A direct `orm.read(...)` always hits the server.
 *
 * Wired in the era.seo.suite.hub form view as
 *
 *     <field name="is_article_pending"
 *            widget="era_auto_refresh_when_true"
 *            invisible="1"/>
 */

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const POLL_MS = 3000;

class EraAutoRefreshWhenTrue extends Component {
    static template = xml`<span class="d-none"/>`;
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this._timer = null;
        this._ticking = false;

        onMounted(() => this._maybeStart());
        onWillUnmount(() => this._stop());
    }

    /** Server-side value as of the last loaded record. */
    _initialValue() {
        return Boolean(this.props.record?.data?.[this.props.name]);
    }

    _maybeStart() {
        if (this._timer) return;
        if (!this._initialValue()) return;
        this._timer = window.setInterval(() => this._tick(), POLL_MS);
    }

    _stop() {
        if (this._timer) {
            window.clearInterval(this._timer);
            this._timer = null;
        }
    }

    async _tick() {
        // Don't overlap ticks if a previous read is still in flight.
        if (this._ticking) return;
        this._ticking = true;
        try {
            const resId = this.props.record?.resId;
            const model = this.props.record?.resModel;
            const name = this.props.name;
            if (!resId || !model) return;
            const rows = await this.orm.read(model, [resId], [name]);
            const pending = Boolean(rows?.[0]?.[name]);
            if (!pending) {
                this._stop();
                // Reload the form record so the table + buttons reflect
                // the post the cron just created.
                try {
                    await this.props.record.load();
                } catch (_) {
                    // Reload errors are non-fatal — the next user click
                    // will re-fetch anyway.
                }
            }
        } catch (_) {
            // Transient ORM error — keep polling on the next tick.
        } finally {
            this._ticking = false;
        }
    }
}

registry.category("fields").add("era_auto_refresh_when_true", {
    component: EraAutoRefreshWhenTrue,
    supportedTypes: ["boolean"],
});

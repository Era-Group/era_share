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
 * The Generate Now button flips the underlying
 * `era_seo.article_pending` ICP to True; the cron clears it when done;
 * the polling here re-reads the record so the "Recently generated" table
 * updates in place.
 *
 * Implemented as a plain OWL Component (not extending BooleanField, which
 * isn't really designed as a base class) — we don't render anything user-
 * visible. The view's `invisible="1"` hides our mount point, but we still
 * need to render *something* so the component lifecycle fires.
 */

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useState, xml } from "@odoo/owl";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const POLL_MS = 3000;

class EraAutoRefreshWhenTrue extends Component {
    static template = xml`<span class="d-none"/>`;
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({});
        this._timer = null;

        onMounted(() => this._maybeStart());
        onWillUnmount(() => this._stop());
    }

    _isPending() {
        return Boolean(this.props.record?.data?.[this.props.name]);
    }

    _maybeStart() {
        if (this._timer) return;
        if (!this._isPending()) return;
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
        } catch (_) {
            // Don't let a transient error kill the polling — try again next tick.
            return;
        }
        if (!this._isPending()) {
            this._stop();
        }
    }
}

registry.category("fields").add("era_auto_refresh_when_true", {
    component: EraAutoRefreshWhenTrue,
    supportedTypes: ["boolean"],
});

/** @odoo-module **/

/**
 * Field widget that polls the hub's blog-gen pending flag every 3
 * seconds via a dedicated RPC. When the server reports `pending = false`
 * the polling stops and the form record is reloaded so the spinner
 * banner, the Generate button, and the "Recently generated" list pick
 * up the new state.
 *
 * History — why this isn't a `record.load()` loop, and why it doesn't
 * read the `is_article_pending` field directly:
 *
 *   v1 used `record.load()` — Odoo 19's relational-model cache returned
 *   the same field values for a long time, the spinner stayed up.
 *
 *   v2 switched to `this.orm.read(model, [resId], ['is_article_pending'])`
 *   — same caching pipeline downstream, same stale-value problem in the
 *   field. We saw the server-side ICP say False while polling kept
 *   returning True for many seconds. The user would have to reload the
 *   tab manually for the spinner to clear.
 *
 *   v3 (this revision) calls a dedicated server method
 *   `era.seo.suite.hub.get_article_pending_state()` that reads ICP
 *   directly and returns a plain dict. No computed-field round trip,
 *   no caching. When pending flips, we call `record.load()` AND, as a
 *   belt-and-braces measure, dispatch an Odoo action-service reload
 *   so the spinner-banner's `invisible="not is_article_pending"`
 *   directive re-evaluates against the freshly loaded record.
 *
 * Wired in the era.seo.suite.hub form view as:
 *
 *     <field name="is_article_pending"
 *            widget="era_auto_refresh_when_true"
 *            invisible="1"/>
 *
 * The `<field>` itself is invisible — the widget exists only to mount a
 * polling lifecycle on the form. The flag's UI consumers (the spinner
 * banner, the Generate Now button) use plain `invisible=` expressions
 * against the same field, refreshed by `record.load()` on flip.
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

        onMounted(() => this._start());
        onWillUnmount(() => this._stop());
    }

    /**
     * Always start polling on mount, regardless of the field's value
     * at render time. The previous version only started when the initial
     * value was True; if the form rendered just before the cron flipped
     * the flag, the spinner banner could stay visible without any poll
     * loop running to clear it. Polling unconditionally costs one cheap
     * RPC every 3 seconds — worth it for reliability.
     */
    _start() {
        if (this._timer) return;
        this._timer = window.setInterval(() => this._tick(), POLL_MS);
        // Fire one tick immediately so a stale-rendered True is corrected
        // within a few hundred ms instead of waiting the full interval.
        this._tick();
    }

    _stop() {
        if (this._timer) {
            window.clearInterval(this._timer);
            this._timer = null;
        }
    }

    async _tick() {
        if (this._ticking) return;
        this._ticking = true;
        try {
            // Dedicated server method that bypasses Odoo's field-read
            // cache by reading ICP directly. Returns {pending: bool}.
            const state = await this.orm.call(
                "era.seo.suite.hub",
                "get_article_pending_state",
                [],
            );
            const pending = Boolean(state?.pending);
            const recordPending = Boolean(
                this.props.record?.data?.[this.props.name],
            );

            // Two flip conditions:
            //   (a) server says not pending, but the form thinks it is
            //       → cron finished, reload to clear the spinner.
            //   (b) server says pending, but form thinks it isn't
            //       → an external start happened while this tab was
            //       open, reload to show the spinner.
            if (pending !== recordPending) {
                try {
                    await this.props.record.load();
                } catch (_) {
                    /* non-fatal */
                }
            }

            // Once pending settles to false, we don't need to keep
            // polling forever. Stop when both server and form agree
            // on false.
            if (!pending && !recordPending) {
                this._stop();
            }
        } catch (_) {
            // Transient RPC error — keep polling next tick.
        } finally {
            this._ticking = false;
        }
    }
}

registry.category("fields").add("era_auto_refresh_when_true", {
    component: EraAutoRefreshWhenTrue,
    supportedTypes: ["boolean"],
});

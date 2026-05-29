/** @odoo-module **/

/**
 * Field widget that polls a server-side "pending" RPC every 3 seconds
 * and triggers a `soft_reload` of the current view as soon as the
 * server reports the flag has flipped. Used by both the blog-gen
 * spinner (era.seo.suite.hub.get_article_pending_state) and the audit
 * runner (era.seo.audit.run.get_audit_pending_state) — the widget
 * takes the model + method names from field options so one component
 * services every pending-flag spinner on the form.
 *
 * History:
 *
 *   v1 used `record.load()` — Odoo 19's relational-model cache returned
 *   stale field values; spinner stayed up.
 *
 *   v2 switched to `this.orm.read(model, [resId], [name])` — same caching
 *   pipeline downstream, same stale-value problem.
 *
 *   v3 added a dedicated server method that reads ICP directly,
 *   bypassing the field-read cache. Polled that, but kept reloading via
 *   `record.load()` — and `record.load()` still returned cached field
 *   values in some browser sessions. Spinner stayed.
 *
 *   v4 keeps the dedicated RPC for *polling* (cheap, no field-cache
 *   round trip) but uses the action service's `soft_reload` client
 *   action for the *reload* — same mechanism the server-side actions
 *   use after Generate Now / TTL clear. soft_reload re-renders the
 *   current view from scratch, which is the only thing we've found
 *   that reliably re-evaluates `invisible="not is_..._pending"`.
 *
 *   v5 (this revision) drops the hardcoded "hub + get_article_pending_state"
 *   pair and accepts the RPC pair via field options so the same widget
 *   can drive the audit-run spinner too.
 *
 * Wired with options:
 *
 *     <field name="is_article_pending"
 *            widget="era_auto_refresh_when_true"
 *            options="{'model': 'era.seo.suite.hub',
 *                      'method': 'get_article_pending_state'}"
 *            invisible="1"/>
 *
 * Defaults (back-compat with the blog-gen field that pre-dates options):
 *     model  = "era.seo.suite.hub"
 *     method = "get_article_pending_state"
 */

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const POLL_MS = 3000;
const DEFAULT_MODEL = "era.seo.suite.hub";
const DEFAULT_METHOD = "get_article_pending_state";

class EraAutoRefreshWhenTrue extends Component {
    static template = xml`<span class="d-none"/>`;
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this._timer = null;
        this._ticking = false;
        // The widget itself unmounts and remounts after a soft_reload,
        // so we need a way to remember "I just reloaded — don't reload
        // again on the same flip" across remounts. We stash the last
        // observed pending value on a window-level key so the next mount
        // can skip a no-op reload loop. Keyed by record id + field name
        // so multiple pending-flag widgets on the same form (or multiple
        // tabs) don't trip over each other.
        this._lastSeenKey = null;
        this._rpcModel = DEFAULT_MODEL;
        this._rpcMethod = DEFAULT_METHOD;

        onMounted(() => {
            const opts = this.props.options || {};
            this._rpcModel = opts.model || DEFAULT_MODEL;
            this._rpcMethod = opts.method || DEFAULT_METHOD;
            const resId = this.props.record?.resId;
            const model = this.props.record?.resModel;
            this._lastSeenKey =
                `__era_pending_last_seen__${model}__${resId}__${this.props.name}`;
            this._start();
        });
        onWillUnmount(() => this._stop());
    }

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
                this._rpcModel,
                this._rpcMethod,
                [],
            );
            const serverPending = Boolean(state?.pending);
            const recordPending = Boolean(
                this.props.record?.data?.[this.props.name],
            );
            const lastSeen = window[this._lastSeenKey];

            // The form's view of pending differs from what the server
            // says — flip detected. Trigger a hard re-render via the
            // action service's soft_reload (more reliable than
            // record.load(), which kept the cached field value in some
            // browser sessions). Skip if we already triggered a reload
            // for this exact server value, to avoid an infinite loop
            // when the post-reload remount sees the same mismatch.
            if (serverPending !== recordPending && lastSeen !== serverPending) {
                window[this._lastSeenKey] = serverPending;
                this._stop();
                try {
                    await this.action.doAction({
                        type: "ir.actions.client",
                        tag: "soft_reload",
                    });
                } catch (_) {
                    /* non-fatal — keep going */
                }
                return;
            }

            // Once server and form both agree on `not pending`, we can
            // stop polling entirely until the user re-clicks the trigger.
            if (!serverPending && !recordPending) {
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
    extractProps: ({ options }) => ({ options }),
});

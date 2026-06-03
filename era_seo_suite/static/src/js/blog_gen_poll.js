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
import { Component, onMounted, onWillUnmount, useState, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const POLL_MS = 3000;
const DEFAULT_MODEL = "era.seo.suite.hub";
const DEFAULT_METHOD = "get_article_pending_state";

class EraAutoRefreshWhenTrue extends Component {
    // When the polled RPC returns a live step message (the blog generator
    // publishes "Searching trends…", "Writing the article (1)…", etc.) we render
    // it as a progress banner that updates every tick — no reload. When there's
    // no message (e.g. the audit spinner, which returns only {pending}) we stay
    // an invisible span and let that form's own static banner show.
    static template = xml`
        <t t-if="state.message">
            <div class="alert alert-info d-flex align-items-center gap-2 mb-2 py-2" role="status">
                <span class="fa fa-circle-o-notch fa-spin"/>
                <span t-esc="state.message"/>
            </div>
        </t>
        <span t-else="" class="d-none"/>
    `;
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ message: "" });
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

    _getMark() {
        const v = window[this._lastSeenKey];
        return v && typeof v === "object" ? v : {};
    }
    _setMark(obj) {
        window[this._lastSeenKey] = obj;
    }

    async _tick() {
        if (this._ticking) return;
        this._ticking = true;
        try {
            // Dedicated server method that reads ICP directly. {pending: bool}.
            const state = await this.orm.call(
                this._rpcModel,
                this._rpcMethod,
                [],
            );
            const serverPending = Boolean(state?.pending);
            const recordPending = Boolean(
                this.props.record?.data?.[this.props.name],
            );
            // Live step label (blog generator only); empty otherwise.
            this.state.message = serverPending ? (state?.message || "") : "";

            // Drive the reload off the SERVER-state transition, not the form's
            // cached field. The old logic compared serverPending to the cached
            // recordPending and guarded with lastSeen; once it reloaded, a
            // still-stale recordPending left lastSeen === serverPending, which
            // BLOCKED every later reload — so the spinner stayed up until a
            // manual refresh. This is the bug being fixed.
            if (serverPending) {
                // If the form's OWN field is still False, this run was started
                // OUTSIDE this form (the scheduled cron, another tab, or a
                // server-side trigger), so the non-stored is_article_pending was
                // computed False at load and never re-read — the "Generate"
                // button is showing ALONGSIDE the live banner. Reload ONCE to
                // re-compute the field True and hide the button. The phase guard
                // keeps it to a single reload per episode (after it, the field
                // reads True and this branch is skipped).
                const mark = this._getMark();
                if (
                    !recordPending &&
                    mark.phase !== "pending" &&
                    mark.phase !== "reloading"
                ) {
                    this._setMark({ phase: "reloading" });
                    this._stop();
                    try {
                        await this.action.doAction({
                            type: "ir.actions.client",
                            tag: "soft_reload",
                        });
                    } catch (_) {
                        /* non-fatal */
                    }
                    return;
                }
                // (Still) running — remember we watched it run so the eventual
                // flip to not-pending triggers exactly one reload, even if the
                // form's cached field value never updates on its own.
                this._setMark({ phase: "pending" });
                return; // keep polling
            }

            // serverPending === false.
            const mark = this._getMark();
            const sawPending = mark.phase === "pending";
            if (!sawPending && !recordPending) {
                this._stop(); // nothing pending and no stale spinner to clear
                return;
            }

            // The run just finished (we watched it) OR the form still shows a
            // stale spinner — soft_reload re-renders the view so the cleared
            // ICP flag re-evaluates `invisible="not is_..._pending"`. A small
            // cap stops a field that somehow never clears from reload-storming.
            const now = Date.now();
            const within = now - (mark.ts || 0) < 30000;
            const count = within ? mark.count || 0 : 0;
            if (count >= 3) {
                this._stop(); // give up rather than loop
                return;
            }
            this._setMark({ phase: "reloading", count: count + 1, ts: now });
            this._stop();
            try {
                await this.action.doAction({
                    type: "ir.actions.client",
                    tag: "soft_reload",
                });
            } catch (_) {
                /* non-fatal */
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

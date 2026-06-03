/** @odoo-module **/

/**
 * Field widget that polls a server-side "pending" RPC every few seconds and
 * reflects the live state in the form WITHOUT reloading the whole view.
 *
 * Two modes, selected by field options:
 *
 *   INLINE mode (blog generator) — set when the `action` option is present.
 *   The widget OWNS the whole control: it renders the "Generate" button when
 *   idle and a live progress banner ("Searching trends…", "Writing the
 *   article", …) while a run is in flight, flipping between them purely from
 *   the polled state. Nothing here depends on the form's computed
 *   is_article_pending field, and it NEVER calls soft_reload — so the page no
 *   longer jumps/scrolls on every tick. When a run FINISHES it refreshes only
 *   this record's data (the "Recently generated" table) via record.load(),
 *   which keeps the scroll position.
 *
 *   LEGACY mode (audit runner) — no `action` option. Original behaviour:
 *   render a banner when the RPC returns a message, and soft_reload the view
 *   on the pending→done transition so the findings list refreshes.
 *
 * History: v1 record.load()/v2 orm.read both returned stale computed-field
 * values; v3/v4 polled a dedicated ICP-reading RPC and reloaded via
 * soft_reload; v5 made the RPC pair configurable. This revision (v6) drops the
 * field dependency for the generator entirely — the computed field lagged the
 * direct-read RPC (ormcache), and the resulting mismatch made the old
 * soft_reload logic fire on a loop ("refreshes every little while"). Driving
 * the UI straight off the RPC removes both the loop and the scroll jump.
 *
 * Wired (inline) with:
 *     <field name="is_article_pending"
 *            widget="era_auto_refresh_when_true"
 *            options="{'model': 'era.seo.suite.hub',
 *                      'method': 'get_article_pending_state',
 *                      'action': 'action_generate_blog_article_now',
 *                      'enabled_field': 'setting_ai_enabled',
 *                      'label': 'Generate article now', 'icon': 'fa-magic',
 *                      'preparing': 'Preparing…', 'confirm': '…?'}"
 *            nolabel="1"/>
 */

import { registry } from "@web/core/registry";
import { Component, onMounted, onWillUnmount, useState, xml } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

const POLL_MS = 3000;
const DEFAULT_MODEL = "era.seo.suite.hub";
const DEFAULT_METHOD = "get_article_pending_state";
// Keep the banner up briefly after a click so a slow server/cron round-trip
// can't flicker the button back before the pending flag is observed.
const TRIGGER_GRACE_MS = 8000;

class EraAutoRefreshWhenTrue extends Component {
    static template = xml`
        <t t-if="inline">
            <div t-if="state.pending"
                 class="alert alert-info d-flex align-items-center gap-2 mb-2 py-2"
                 role="status">
                <span class="fa fa-circle-o-notch fa-spin"/>
                <span t-esc="state.message"/>
            </div>
            <button t-elif="inlineEnabled" type="button" class="btn btn-primary"
                    t-on-click="onGenerate">
                <span t-attf-class="fa {{btnIcon}} me-1"/>
                <t t-esc="btnLabel"/>
            </button>
            <span t-else="" class="d-none"/>
        </t>
        <t t-else="">
            <div t-if="state.message"
                 class="alert alert-info d-flex align-items-center gap-2 mb-2 py-2"
                 role="status">
                <span class="fa fa-circle-o-notch fa-spin"/>
                <span t-esc="state.message"/>
            </div>
            <span t-else="" class="d-none"/>
        </t>
    `;
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.dialog = useService("dialog");
        const opts = this.props.options || {};
        this._rpcModel = opts.model || DEFAULT_MODEL;
        this._rpcMethod = opts.method || DEFAULT_METHOD;
        this._action = opts.action || null;          // inline mode iff present
        this._enabledField = opts.enabled_field || null;
        this._preparing = opts.preparing || "…";
        this._confirm = opts.confirm || "";
        this.btnLabel = opts.label || "Generate";
        this.btnIcon = opts.icon || "fa-magic";
        this.state = useState({ pending: false, message: "" });
        this._timer = null;
        this._ticking = false;
        this._wasPending = false;
        this._graceUntil = 0;
        // Legacy-mode reload bookkeeping (kept per record id + field name so
        // multiple spinners / tabs don't trip over each other).
        this._lastSeenKey = null;

        onMounted(() => {
            const resId = this.props.record?.resId;
            const model = this.props.record?.resModel;
            this._lastSeenKey =
                `__era_pending_last_seen__${model}__${resId}__${this.props.name}`;
            if (this.inline) {
                const initial = Boolean(this.props.record?.data?.[this.props.name]);
                this.state.pending = initial;
                this._wasPending = initial;
            }
            this._start();
        });
        onWillUnmount(() => this._stop());
    }

    get inline() {
        return Boolean(this._action);
    }
    get inlineEnabled() {
        return (
            !this._enabledField ||
            Boolean(this.props.record?.data?.[this._enabledField])
        );
    }

    _start() {
        if (this._timer) return;
        this._timer = window.setInterval(() => this._tick(), POLL_MS);
        this._tick(); // fire one immediately
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

    onGenerate() {
        const proceed = async () => {
            // Show the banner straight away (with a short grace window) so the
            // button doesn't flash back before the server flag is observed.
            this._graceUntil = Date.now() + TRIGGER_GRACE_MS;
            this.state.pending = true;
            this.state.message = this._preparing;
            this._wasPending = true;
            let result;
            try {
                // Server flips the ICP flag + enqueues the cron; it returns a
                // soft_reload (ignored — we update in place) OR, if generation
                // is disabled, a display_notification we should surface.
                result = await this.orm.call(this._rpcModel, this._action, [
                    [this.props.record.resId],
                ]);
            } catch (_) {
                // Surfaced by the web client; let polling reconcile the state.
                return;
            }
            if (result && result.tag === "display_notification") {
                // Declined (e.g. generation is off) — drop the optimistic
                // banner and show the message instead of spinning.
                this._graceUntil = 0;
                this._wasPending = false;
                this.state.pending = false;
                this.state.message = "";
                this.action.doAction(result);
            }
        };
        if (this._confirm) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Confirm"),
                body: this._confirm,
                confirm: proceed,
                cancel: () => {},
            });
        } else {
            proceed();
        }
    }

    async _tick() {
        if (this._ticking) return;
        this._ticking = true;
        try {
            const rpc = await this.orm.call(this._rpcModel, this._rpcMethod, []);
            const serverPending = Boolean(rpc?.pending);

            if (this.inline) {
                // Drive everything off the live RPC. No field read, no reload.
                const effective =
                    serverPending || Date.now() < this._graceUntil;
                const finished = this._wasPending && !effective;
                this._wasPending = effective;
                this.state.pending = effective;
                this.state.message = effective
                    ? rpc?.message || this._preparing
                    : "";
                if (finished) {
                    // Run just completed — refresh ONLY this record's data so
                    // the "Recently generated" table shows the new article.
                    // record.load() updates in place and keeps the scroll
                    // position (soft_reload would jump to the top).
                    try {
                        await this.props.record.load();
                    } catch (_) {
                        /* non-fatal */
                    }
                }
                return;
            }

            // ---- LEGACY mode (audit / back-compat): banner + soft_reload ----
            const recordPending = Boolean(
                this.props.record?.data?.[this.props.name],
            );
            this.state.message = serverPending ? rpc?.message || "" : "";
            if (serverPending) {
                this._setMark({ phase: "pending" });
                return;
            }
            const mark = this._getMark();
            const sawPending = mark.phase === "pending";
            if (!sawPending && !recordPending) {
                this._stop();
                return;
            }
            const now = Date.now();
            const within = now - (mark.ts || 0) < 30000;
            const count = within ? mark.count || 0 : 0;
            if (count >= 3) {
                this._stop();
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

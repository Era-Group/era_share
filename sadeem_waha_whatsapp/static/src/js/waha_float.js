/** @odoo-module **/

import { Component, useState, xml, onMounted, reactive } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { WahaChatAction } from "./waha_chat_action";

// Shared state for the single floating chat window. One window at a time —
// opening another session just swaps which session it shows.
const state = reactive({
    open: false,
    minimized: false,
    sessionId: null,
    sessionName: "",
});

// Service: lets the menu button (via the client action) drive the window.
const wahaFloatService = {
    start() {
        return {
            open(sessionId, sessionName) {
                // Re-key the inner component so it reloads for a new session.
                state.sessionId = sessionId;
                state.sessionName = sessionName || "";
                state.open = true;
                state.minimized = false;
            },
        };
    },
};
registry.category("services").add("waha_float", wahaFloatService);

// Thin client action: the form button opens this; it just delegates to the
// service and stays on the current page (no full-screen action).
class WahaFloatLauncher extends Component {
    static template = xml`<div/>`;
    static props = ["*"];
    setup() {
        const float = useService("waha_float");
        const action = useService("action");
        const ctx = (this.props.action || {}).context || {};
        onMounted(() => {
            float.open(ctx.session_id, ctx.session_name);
            // Close this launcher dialog — the window floats over the page.
            action.doAction({ type: "ir.actions.act_window_close" });
        });
    }
}
registry.category("actions").add("sadeem_waha_chat.float_open", WahaFloatLauncher);

// The floating window itself, mounted once globally so it survives navigation.
export class WahaFloatWindow extends Component {
    static template = "sadeem_waha_chat.WahaFloatWindow";
    static components = { WahaChatAction };
    static props = {};

    setup() {
        this.state = useState(state);
        // Window geometry (px). resize handled by CSS `resize: both`.
        this.pos = useState({ top: 80, left: null, width: 760, height: 560 });
        this._drag = null;
    }

    get actionProps() {
        // Feed the inner chat component the same shape it gets as an action.
        return {
            action: {
                context: {
                    session_id: this.state.sessionId,
                    session_name: this.state.sessionName,
                },
            },
        };
    }

    minimize() {
        this.state.minimized = true;
    }
    restore() {
        this.state.minimized = false;
    }
    close() {
        this.state.open = false;
    }

    // ---- drag by header ----
    onHeaderPointerDown(ev) {
        if (ev.target.closest(".o_waha_float_btn")) {
            return; // don't drag when hitting a control
        }
        const rect = ev.currentTarget.closest(".o_waha_float").getBoundingClientRect();
        this._drag = {
            dx: ev.clientX - rect.left,
            dy: ev.clientY - rect.top,
        };
        this.pos.left = rect.left;
        this.pos.top = rect.top;
        const move = (e) => {
            if (!this._drag) {
                return;
            }
            this.pos.left = Math.max(0, e.clientX - this._drag.dx);
            this.pos.top = Math.max(0, e.clientY - this._drag.dy);
        };
        const up = () => {
            this._drag = null;
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }

    // ---- resize by corner handle ----
    onResizePointerDown(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const start = {
            x: ev.clientX,
            y: ev.clientY,
            w: this.pos.width,
            h: this.pos.height,
        };
        const move = (e) => {
            this.pos.width = Math.max(360, start.w + (e.clientX - start.x));
            this.pos.height = Math.max(320, start.h + (e.clientY - start.y));
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }

    get style() {
        const left = this.pos.left == null ? "auto" : `${this.pos.left}px`;
        const right = this.pos.left == null ? "24px" : "auto";
        return (
            `top:${this.pos.top}px;left:${left};right:${right};` +
            `width:${this.pos.width}px;height:${this.pos.height}px;`
        );
    }
}

registry.category("main_components").add("WahaFloatWindow", {
    Component: WahaFloatWindow,
});

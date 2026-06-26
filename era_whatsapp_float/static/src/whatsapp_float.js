/** @odoo-module **/

import {
    Component,
    onWillUnmount,
    reactive,
    useEffect,
    useRef,
    useState,
    useSubEnv,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { useMovable } from "@mail/utils/common/hooks";
import { Discuss } from "@mail/core/public_web/discuss";
import { DiscussSidebarChannel } from "@mail/discuss/core/public_web/discuss_sidebar_categories";

// Single shared, reactive state for the one-and-only floating window. It is
// created in the service `start()` and shared with the window component so the
// systray button and any other caller all drive the same window.
// `allowed` gates the whole widget to WhatsApp users; it flips to true once the
// group membership resolves (see the service `start()` below).
const floatState = reactive({
    open: false,
    minimized: false,
    allowed: false,
    targetChannelId: null, // set by openChannel() to navigate on next show
});

// The official `whatsapp` (EE) module's access group.
const WHATSAPP_GROUP = "whatsapp.group_whatsapp_admin";

/**
 * Floating WhatsApp window. Registered once globally through `main_components`
 * (same mechanism as the Discuss ChatHub), so it lives on top of every backend
 * page and survives navigation.
 *
 * It embeds the genuine Discuss content pane (`<Discuss hasSidebar="false"/>`)
 * and renders a WhatsApp-only conversation list on the side, reusing the native
 * `DiscussSidebarChannel` item for full visual + behavioural fidelity.
 */
export class WhatsappFloatWindow extends Component {
    static template = "era_whatsapp_float.Window";
    static components = { Discuss, DiscussSidebarChannel };
    static props = {};

    setup() {
        this.store = useService("mail.store");
        this.ui = useService("ui");
        this.state = useState(floatState);
        this.rootRef = useRef("root");

        // Window geometry. `top`/`left` stay "unset" until the user drags, so the
        // window keeps its default bottom-right anchor (WhatsApp convention).
        this.position = useState({
            top: "unset",
            left: "unset",
            bottom: "84px",
            right: "24px",
            width: 760,
            height: 600,
        });

        // Window-local layout options. `membersHidden` collapses the Discuss
        // member-list inspector so the conversation gets the full width; it is
        // hidden by default in the narrow float.
        this.layout = useState({ membersHidden: true });

        // Reused `DiscussSidebarChannel` items decide between "select in Discuss"
        // and "open a chat window" based on `inDiscussApp`; force the former.
        useSubEnv({ inDiscussApp: true });

        // Tracks the `store.discuss.isActive` value we override while open, so we
        // can restore it (the real Discuss client action owns that flag too).
        this._previousIsActive = undefined;

        // The whole window (`.o_era_wa_float`) moves, but it is grabbed only by
        // the header — and never by the header's buttons. Same shape as the
        // movable Odoo Dialog (elements + handle + ignore).
        useMovable({
            cursor: "grabbing",
            ref: this.rootRef,
            elements: ".o_era_wa_float",
            handle: ".o_era_wa_float_header",
            ignore: ".o_era_wa_float_btn",
            onDrop: ({ top, left }) => {
                this.position.bottom = "unset";
                this.position.right = "unset";
                this.position.top = `${top}px`;
                this.position.left = `${left}px`;
            },
        });

        // React to the open/minimized transitions: (de)activate the Discuss store
        // and make sure a WhatsApp conversation is selected.
        useEffect(
            (shown) => {
                if (shown) {
                    this._activate();
                } else {
                    this._deactivate();
                }
            },
            () => [this.isShown]
        );

        // When a specific channel was requested (via openChannel()), navigate to it
        // once it becomes available in the store (channels may still be loading).
        useEffect(
            () => {
                if (!this.state.targetChannelId || !this.isShown) {
                    return;
                }
                const thread = this.store.Thread.get({
                    model: "discuss.channel",
                    id: this.state.targetChannelId,
                });
                if (thread) {
                    thread.setAsDiscussThread(false);
                    this.state.targetChannelId = null;
                }
            },
            () => [
                this.state.targetChannelId,
                this.state.open,
                this.state.minimized,
                this.whatsappThreads.length,
            ]
        );

        onWillUnmount(() => this._deactivate());
    }

    get isShown() {
        return this.state.open && !this.state.minimized;
    }

    /** WhatsApp category provided by the official `whatsapp` module. */
    get whatsappCategory() {
        return this.store.discuss.whatsapp;
    }

    /** Conversations to list in the WhatsApp-only sidebar (most recent first). */
    get whatsappThreads() {
        const category = this.whatsappCategory;
        if (!category) {
            return [];
        }
        return category.threads.filter((thread) => thread.displayInSidebar);
    }

    _activate() {
        // Make sure channels are loaded so the WhatsApp list can populate.
        if (this.store.channels?.status === "not_fetched") {
            this.store.channels.fetch();
        }
        if (this._previousIsActive === undefined) {
            this._previousIsActive = this.store.discuss.isActive;
        }
        this.store.discuss.isActive = true;
        // When a specific target was requested, let the dedicated useEffect handle
        // navigation (it re-fires once channels are loaded). Otherwise auto-select
        // the first WhatsApp conversation if no WhatsApp thread is active.
        if (!this.state.targetChannelId) {
            const current = this.store.discuss.thread;
            if (!current || current.channel_type !== "whatsapp") {
                this.whatsappThreads[0]?.setAsDiscussThread(false);
            }
        }
    }

    _deactivate() {
        if (this._previousIsActive !== undefined) {
            this.store.discuss.isActive = this._previousIsActive;
            this._previousIsActive = undefined;
        }
    }

    onMinimize() {
        this.state.minimized = true;
    }

    onClose() {
        this.state.open = false;
        this.state.minimized = false;
    }

    /**
     * Show/hide the Discuss member-list inspector inside the float. Hiding is a
     * pure CSS collapse (reclaims the conversation width). When revealing, if no
     * member panel is currently mounted, open it through the genuine Discuss
     * "Members" header action (found by its language-independent icon).
     */
    toggleMembers() {
        this.layout.membersHidden = !this.layout.membersHidden;
        if (this.layout.membersHidden) {
            return;
        }
        const root = this.rootRef.el;
        if (root && !root.querySelector(".o-discuss-ChannelMemberList")) {
            const button = [...root.querySelectorAll("button .oi-users")]
                .map((icon) => icon.closest("button"))
                .find(Boolean);
            button?.click();
        }
    }

    // ---- manual corner resize (bottom-start corner) ----
    onResizePointerDown(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        const start = {
            x: ev.clientX,
            y: ev.clientY,
            w: this.position.width,
            h: this.position.height,
        };
        const onMove = (e) => {
            this.position.width = Math.max(420, start.w + (e.clientX - start.x));
            this.position.height = Math.max(360, start.h + (e.clientY - start.y));
        };
        const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
    }
}

export const whatsappFloatService = {
    dependencies: ["mail.store", "ui"],
    start() {
        registry
            .category("main_components")
            .add("era_whatsapp_float.Window", { Component: WhatsappFloatWindow });
        // Reveal the window/systray only for WhatsApp users.
        user.hasGroup(WHATSAPP_GROUP).then((hasAccess) => {
            floatState.allowed = hasAccess;
        });
        return {
            state: floatState,
            open() {
                floatState.open = true;
                floatState.minimized = false;
            },
            close() {
                floatState.open = false;
                floatState.minimized = false;
            },
            toggle() {
                if (floatState.open && !floatState.minimized) {
                    floatState.minimized = true;
                } else {
                    floatState.open = true;
                    floatState.minimized = false;
                }
            },
            openChannel(channelId) {
                floatState.targetChannelId = channelId;
                floatState.open = true;
                floatState.minimized = false;
            },
        };
    },
};
registry.category("services").add("era_whatsapp_float", whatsappFloatService);

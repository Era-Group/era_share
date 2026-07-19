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
import { compareDatetime } from "@mail/utils/common/misc";
import { Discuss } from "@mail/core/public_web/discuss";
import { DiscussSidebarChannel } from "@mail/discuss/core/public_web/discuss_sidebar_categories";

// Single shared, reactive state for the one-and-only floating window.
// `allowed` gates the whole widget to WhatsApp users; it flips to true once the
// group membership resolves (see the service `start()` below).
const floatState = reactive({
    open: false,
    minimized: false,
    allowed: false,
    targetChannelId: null,
    // Conversation list filter: "all" (WAHA + standard), "waha", or "standard".
    listMode: "all",
});

// The floating window is shown to any user in the WhatsApp (WAHA) group.
const WHATSAPP_GROUP = "whatsapp.group_whatsapp_admin";

/**
 * Floating WhatsApp/WAHA window. Registered once globally through
 * `main_components`, so it lives on top of every backend page and survives
 * navigation. It embeds the genuine Discuss content pane and renders a
 * conversation list on the side, with a toggle to show WAHA channels, standard
 * (Meta) WhatsApp channels, or both.
 */
export class WahaFloatWindow extends Component {
    static template = "era_waha_integration.FloatWindow";
    static components = { Discuss, DiscussSidebarChannel };
    static props = {};

    setup() {
        this.store = useService("mail.store");
        this.ui = useService("ui");
        this.orm = useService("orm");
        this.state = useState(floatState);
        // WAHA account health, refreshed each time the window opens (see _activate).
        this.health = useState({ score: null, label: "good", name: "" });
        this.rootRef = useRef("root");

        this.position = useState({
            top: "unset",
            left: "unset",
            bottom: "84px",
            right: "24px",
            width: 760,
            height: 600,
        });

        this.layout = useState({ membersHidden: true });

        useSubEnv({ inDiscussApp: true });

        this._previousIsActive = undefined;

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
                this.conversationThreads.length,
            ]
        );

        onWillUnmount(() => this._deactivate());
    }

    get isShown() {
        return this.state.open && !this.state.minimized;
    }

    /** WAHA channels category (added by this module). */
    get wahaThreads() {
        return this.store.discuss.waha?.threads ?? [];
    }

    /** Official/standard WhatsApp channels category. */
    get standardThreads() {
        return this.store.discuss.whatsapp?.threads ?? [];
    }

    /** Conversations to list, filtered by the selected mode (most recent first). */
    get conversationThreads() {
        let threads;
        if (this.state.listMode === "waha") {
            threads = this.wahaThreads;
        } else if (this.state.listMode === "standard") {
            threads = this.standardThreads;
        } else {
            threads = [...this.wahaThreads, ...this.standardThreads];
        }
        return threads
            .filter((thread) => thread.displayInSidebar)
            .sort((a, b) => compareDatetime(b.lastInterestDt, a.lastInterestDt) || b.id - a.id);
    }

    setListMode(mode) {
        this.state.listMode = mode;
    }

    /** Load the WAHA account health, surfacing the most at-risk account. */
    async _loadHealth() {
        try {
            const rows = await this.orm.searchRead(
                "whatsapp.account",
                [["provider", "=", "waha"]],
                ["name", "waha_health_score", "waha_health_label"]
            );
            if (!rows.length) {
                this.health.score = null;
                return;
            }
            const worst = rows.reduce((a, b) =>
                b.waha_health_score < a.waha_health_score ? b : a
            );
            this.health.score = worst.waha_health_score;
            this.health.label = worst.waha_health_label || "good";
            this.health.name = worst.name || "";
        } catch {
            this.health.score = null;
        }
    }

    _activate() {
        this._loadHealth();
        if (this.store.channels?.status === "not_fetched") {
            this.store.channels.fetch();
        }
        if (this._previousIsActive === undefined) {
            this._previousIsActive = this.store.discuss.isActive;
        }
        this.store.discuss.isActive = true;
        if (!this.state.targetChannelId) {
            const current = this.store.discuss.thread;
            if (!current || current.channel_type !== "whatsapp") {
                this.conversationThreads[0]?.setAsDiscussThread(false);
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

export const wahaFloatService = {
    dependencies: ["mail.store", "ui"],
    start() {
        registry
            .category("main_components")
            .add("era_waha_integration.FloatWindow", { Component: WahaFloatWindow });
        user.hasGroup(WHATSAPP_GROUP).then((allowed) => {
            floatState.allowed = allowed;
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
registry.category("services").add("era_waha_float", wahaFloatService);

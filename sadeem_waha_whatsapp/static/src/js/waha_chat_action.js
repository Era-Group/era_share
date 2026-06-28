/** @odoo-module **/

import { Component, useState, onWillStart, onMounted, useRef } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

const CHAT_LIMIT = 20;
const MSG_LIMIT = 50;

export class WahaChatAction extends Component {
    static template = "sadeem_waha_chat.WahaChatAction";
    static props = ["*"];

    setup() {
        const action = this.props.action || {};
        const ctx = action.context || {};
        const params = action.params || {};
        this.sessionId = ctx.session_id ?? params.session_id;
        this.sessionName = ctx.session_name ?? params.session_name ?? "";

        this.notification = useService("notification");
        this.busService = useService("bus_service");
        this.threadRef = useRef("thread");

        this.state = useState({
            chats: [],
            chatOffset: 0,
            chatsHasMore: true,
            chatsLoading: false,
            chatsError: "",
            canRetry: false,

            activeChatId: null,
            messages: [],
            msgOffset: 0,
            msgHasMore: true,
            msgLoading: false,
            composer: "",
            // waha_message_id -> emoji string ('' = none). One reaction per message
            // (WhatsApp replaces, doesn't stack).
            reactions: {},
            // waha_message_id currently showing the emoji picker, or null.
            pickerFor: null,
        });
        this.fileRef = useRef("file");
        this.EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🙏"];
        this.stickToBottom = true;

        onWillStart(async () => {
            await this.loadChats();
        });

        onMounted(() => {
            if (!this.sessionId) {
                return; // no session in context — nothing to subscribe to
            }
            const channel = `sadeem_waha_chat/session_${this.sessionId}`;
            this.busService.addChannel(channel);
            this.busService.subscribe("sadeem_waha_chat/new_message", (p) =>
                this.onBusNewMessage(p)
            );
            this.busService.subscribe("sadeem_waha_chat/message_ack", (p) =>
                this.onBusAck(p)
            );
            this.busService.subscribe("sadeem_waha_chat/message_reaction", (p) =>
                this.onBusReaction(p)
            );
        });
    }

    // ---- chat list ----
    async loadChats() {
        if (this.state.chatsLoading || !this.state.chatsHasMore) {
            return;
        }
        if (!this.sessionId) {
            this.state.chatsError = _t("No session was provided.");
            this.state.chatsHasMore = false;
            return;
        }
        this.state.chatsLoading = true;
        try {
            const res = await rpc("/sadeem_waha_chat/chats/overview", {
                session_id: this.sessionId,
                limit: CHAT_LIMIT,
                offset: this.state.chatOffset,
            });
            if (!res.success) {
                // store_disabled carries an actionable message; everything else
                // gets a fixed generic line (never the raw upstream error).
                this.state.chatsError = res.store_disabled
                    ? res.error
                    : _t("Could not reach the WhatsApp server.");
                this.state.canRetry = !res.store_disabled;
                this.state.chatsHasMore = false;
                return;
            }
            this.state.chats.push(...res.chats);
            this.state.chatOffset += CHAT_LIMIT;
            this.state.chatsHasMore = res.has_more;
        } catch (e) {
            this.state.chatsError = _t("Could not reach the WhatsApp server.");
            this.state.canRetry = true;
            this.state.chatsHasMore = false;
        } finally {
            this.state.chatsLoading = false;
        }
    }

    async retryChats() {
        this.state.chatsError = "";
        this.state.canRetry = false;
        this.state.chatsHasMore = true; // re-open the gate closed on failure
        await this.loadChats();
    }

    onChatScroll(ev) {
        const el = ev.target;
        if (el.scrollTop + el.clientHeight >= el.scrollHeight - 40) {
            this.loadChats();
        }
    }

    // ---- messages ----
    async openChat(chat) {
        if (this.state.activeChatId === chat.id) {
            return;
        }
        this.state.activeChatId = chat.id;
        chat.unread = 0; // opening clears the badge
        this.state.messages = [];
        this.state.msgOffset = 0;
        this.state.msgHasMore = true;
        // Stay pinned to the bottom until the user scrolls up — media that
        // finishes loading after render keeps the view on the latest message.
        this.stickToBottom = true;
        await this.loadMessages();
        this.markSeen(chat.id);
        this.scrollThreadToBottom();
    }

    /** Re-pin to bottom when an image/audio finishes loading mid-open. */
    onMediaLoad() {
        if (this.stickToBottom) {
            this.scrollThreadToBottom();
        }
    }

    async loadMessages() {
        if (this.state.msgLoading || !this.state.msgHasMore || !this.state.activeChatId) {
            return;
        }
        this.state.msgLoading = true;
        const chatId = this.state.activeChatId;
        try {
            const res = await rpc("/waha/chat/messages/live", {
                chat_id: chatId,
                session_id: this.sessionId,
                limit: MSG_LIMIT,
                offset: this.state.msgOffset,
            });
            if (chatId !== this.state.activeChatId) {
                return; // user switched chats mid-flight
            }
            const batch = (res && res.success && res.messages) || [];
            // Seed reactions carried by each message so they persist across
            // reopen/older-page loads (the bus only delivers live ones).
            for (const m of batch) {
                if (m.waha_message_id && m.reaction) {
                    this.state.reactions[m.waha_message_id] = m.reaction;
                }
            }
            // WAHA returns newest-first; prepend older pages above existing.
            const ordered = batch.slice().reverse();
            this.state.messages = ordered.concat(this.state.messages);
            // Per docs: always increment offset by limit.
            this.state.msgOffset += MSG_LIMIT;
            this.state.msgHasMore = batch.length === MSG_LIMIT;
        } catch (e) {
            this.notification.add(e.message || _t("Failed to load messages."), {
                type: "danger",
            });
            this.state.msgHasMore = false;
        } finally {
            this.state.msgLoading = false;
        }
    }

    onThreadScroll(ev) {
        const el = ev.target;
        // Once the user leaves the bottom, stop auto-pinning to it.
        const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        this.stickToBottom = atBottom;
        // Older messages load when scrolled to the top.
        if (el.scrollTop <= 40) {
            this.loadMessages();
        }
    }

    async sendMessage(file = null) {
        const text = (this.state.composer || "").trim();
        if ((!text && !file) || !this.state.activeChatId) {
            return;
        }
        const chatId = this.state.activeChatId;
        const phone = chatId.split("@")[0];
        const params = {
            chat_id: chatId,
            session_id: this.sessionId,
            text,
            phone_number: phone,
        };
        if (file) {
            params.file_data = file.data;
            params.file_name = file.name;
            params.file_mimetype = file.mimetype;
        }
        this.state.composer = "";
        try {
            const res = await rpc("/waha/chat/send", params);
            if (res && res.success) {
                const m = res.message || {};
                // Optimistic: bus will reconcile ack/status.
                this.state.messages.push({
                    waha_message_id: m.waha_message_id,
                    text,
                    direction: "outgoing",
                    status: "sent",
                    create_date: "",
                    has_attachment: m.has_attachment,
                    attachment_name: m.attachment_name,
                    attachment_url: m.attachment_url,
                    message_type: m.message_type,
                });
                this.stickToBottom = true;
                this.scrollThreadToBottom();
            } else {
                this.notification.add(
                    (res && res.error) || _t("Failed to send message."),
                    { type: "danger" }
                );
            }
        } catch (e) {
            this.notification.add(e.message || _t("Failed to send."), { type: "danger" });
        }
    }

    onComposerKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    // ---- attachments ----
    pickFile() {
        if (this.fileRef.el) {
            this.fileRef.el.click();
        }
    }

    async onFileChange(ev) {
        const file = ev.target.files && ev.target.files[0];
        if (!file) {
            return;
        }
        try {
            const data = await this._readBase64(file);
            await this.sendMessage({ data, name: file.name, mimetype: file.type });
        } catch (e) {
            this.notification.add(_t("Could not read the file."), { type: "danger" });
        } finally {
            ev.target.value = ""; // allow re-picking the same file
        }
    }

    _readBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1] || "");
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    // ---- reactions ----
    togglePicker(msgId) {
        this.state.pickerFor = this.state.pickerFor === msgId ? null : msgId;
    }

    async react(msg, emoji) {
        const msgId = msg.waha_message_id;
        this.state.pickerFor = null;
        if (!msgId) {
            return;
        }
        // Tapping the existing reaction removes it (send empty string).
        const next = this.state.reactions[msgId] === emoji ? "" : emoji;
        const prev = this.state.reactions[msgId] || "";
        this.state.reactions[msgId] = next; // optimistic
        try {
            const res = await rpc("/sadeem_waha_chat/react", {
                session_id: this.sessionId,
                chat_id: this.state.activeChatId,
                message_id: msgId,
                reaction: next,
            });
            if (!res || !res.success) {
                this.state.reactions[msgId] = prev; // revert
                this.notification.add(
                    (res && res.error) || _t("Could not send the reaction."),
                    { type: "danger" }
                );
            }
        } catch (e) {
            this.state.reactions[msgId] = prev;
            this.notification.add(_t("Could not send the reaction."), { type: "danger" });
        }
    }

    async markSeen(chatId) {
        try {
            await rpc("/waha/chat/send_seen", {
                chat_id: chatId,
                session_id: this.sessionId,
                message_ids: [],
            });
        } catch (e) {
            // Non-critical.
        }
    }

    // ---- realtime ----
    /** Digits of a chat id / phone, for matching @lid vs @c.us vs raw phone. */
    _phoneOf(value) {
        return (value || "").split("@")[0].replace(/\D/g, "");
    }

    /** A bus payload belongs to the open chat if its chat_id matches, or its
        resolved phone matches the active chat's phone. Incoming messages arrive
        with an @lid chat_id but a resolved phone_number — phone is the reliable key. */
    belongsToActiveChat(payload) {
        if (!this.state.activeChatId) {
            return false;
        }
        if (payload.chat_id && payload.chat_id === this.state.activeChatId) {
            return true;
        }
        const active = this._phoneOf(this.state.activeChatId);
        return !!payload.phone_number && this._phoneOf(payload.phone_number) === active;
    }

    onBusNewMessage(payload) {
        // Bump the chat in the list (match by id or resolved phone).
        const phone = this._phoneOf(payload.phone_number);
        const chat = this.state.chats.find(
            (c) => c.id === payload.chat_id || this._phoneOf(c.id) === phone
        );
        if (chat) {
            chat.last_message = {
                body: payload.text,
                fromMe: payload.direction === "outgoing",
                timestamp: 0,
            };
            // Bump the unread badge for incoming messages on a non-open chat.
            if (payload.direction === "incoming" && chat.id !== this.state.activeChatId) {
                chat.unread = (chat.unread || 0) + 1;
            }
            const idx = this.state.chats.indexOf(chat);
            this.state.chats.splice(idx, 1);
            this.state.chats.unshift(chat);
        }
        if (this.belongsToActiveChat(payload)) {
            // Avoid duplicating an optimistic outgoing echo.
            const exists =
                payload.waha_message_id &&
                this.state.messages.some(
                    (m) => m.waha_message_id === payload.waha_message_id
                );
            if (!exists) {
                this.state.messages.push({
                    waha_message_id: payload.waha_message_id,
                    text: payload.text,
                    direction: payload.direction,
                    status: payload.status,
                    create_date: payload.create_date,
                });
                this.scrollThreadToBottom();
            }
            if (payload.direction === "incoming") {
                this.markSeen(this.state.activeChatId);
            }
        }
    }

    onBusAck(payload) {
        // The WhatsApp message hash is globally unique, so bind by it directly —
        // no chat match needed (ack pushes may lack a resolved phone). If the
        // hash isn't among the loaded messages, it's for another chat: ignore.
        const hashOf = (id) => (id || "").split("_").pop();
        const wantHash = payload.msg_hash || hashOf(payload.waha_message_id);
        if (!wantHash) {
            return;
        }
        const msg = this.state.messages.find(
            (m) =>
                m.waha_message_id === payload.waha_message_id ||
                hashOf(m.waha_message_id) === wantHash
        );
        if (msg) {
            msg.status = payload.status;
            msg.ack = payload.ack;
        }
    }

    onBusReaction(payload) {
        if (payload.chat_id && payload.chat_id !== this.state.activeChatId) {
            return;
        }
        // '' clears the reaction.
        this.state.reactions[payload.message_id] = payload.reaction || "";
    }

    // ---- helpers ----
    scrollThreadToBottom() {
        // Scroll after OWL has rendered and the browser has laid out the new
        // content (a microtask runs too early — scrollHeight is still stale).
        // A second frame catches late layout from images/audio.
        const jump = () => {
            const el = this.threadRef.el;
            if (el) {
                el.scrollTop = el.scrollHeight;
            }
        };
        requestAnimationFrame(() => {
            jump();
            requestAnimationFrame(jump);
        });
    }

    /** True when an attachment should render as an inline image preview. */
    isImage(msg) {
        if (!msg.has_attachment) {
            return false;
        }
        if (msg.message_type === "image") {
            return true;
        }
        const mt = msg.attachment_mimetype || "";
        return mt.startsWith("image/");
    }

    /** True when an attachment should render as an audio player (voice notes). */
    isAudio(msg) {
        if (!msg.has_attachment) {
            return false;
        }
        if (msg.message_type === "voice" || msg.message_type === "audio") {
            return true;
        }
        const mt = msg.attachment_mimetype || "";
        return mt.startsWith("audio/");
    }

    /** Map WhatsApp ack/status to a tick glyph + style. */
    tick(msg) {
        if (msg.direction !== "outgoing") {
            return null;
        }
        const ack = msg.ack;
        const status = msg.status;
        if (ack === -1 || status === "failed") {
            return { glyph: "✗", cls: "o_waha_tick_error" };
        }
        if (ack === 0 || status === "draft") {
            return { glyph: "🕐", cls: "o_waha_tick_pending" };
        }
        if (ack >= 3 || status === "read") {
            return { glyph: "✓✓", cls: "o_waha_tick_read" };
        }
        if (ack === 2 || status === "delivered") {
            return { glyph: "✓✓", cls: "o_waha_tick_delivered" };
        }
        return { glyph: "✓", cls: "o_waha_tick_sent" };
    }
}

registry.category("actions").add("sadeem_waha_chat.chat", WahaChatAction);

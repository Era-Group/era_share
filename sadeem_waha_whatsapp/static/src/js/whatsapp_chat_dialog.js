/** @odoo-module **/

import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { _t } from "@web/core/l10n/translation";

export class WhatsappChatDialog extends Component {
    static template = "sadeem_waha_whatsapp.WhatsappChatDialog";
    static props = {
        chatId: String,
        sessionId: Number,
        phoneNumber: String,
        partnerId: { type: Number, optional: true },
        partnerName: String,
        close: Function,
    };

    setup() {
        this.notification = useService("notification");
        this.messagesRef = useRef("messages");
        this.fileInputRef = useRef("fileInput");
        this.windowRef = useRef("floatWindow");

        this.state = useState({
            messages: [],
            text: "",
            loading: true,
            sending: false,
            isFallback: false,
            hasMore: false,
            loadingMore: false,
            minimized: false,
            dragging: false,
            attachFile: null,
        });

        this._pollInterval = null;
        this._statusInterval = null;
        // drag state (plain object, not reactive — no render needed)
        this._drag = { active: false, startX: 0, startY: 0, origLeft: 0, origTop: 0 };

        this._initialLoad = true;

        onMounted(async () => {
            await this._loadMessages();
            this._pollInterval = setInterval(() => this._pollMessages(), 3000);
            this._statusInterval = setInterval(() => this._loadMessages(false), 5000);
        });

        onWillUnmount(() => {
            clearInterval(this._pollInterval);
            clearInterval(this._statusInterval);
            // clean up any lingering drag listeners
            document.removeEventListener("mousemove", this._onDragMove);
            document.removeEventListener("mouseup", this._onDragEnd);
        });

        // bind once so removeEventListener works
        this._onDragMove = this._onDragMove.bind(this);
        this._onDragEnd = this._onDragEnd.bind(this);
    }

    // ── Contact URL ──────────────────────────────────────────────
    get contactUrl() {
        if (this.props.partnerId) {
            return `/odoo/contacts/${this.props.partnerId}`;
        }
        return null;
    }

    // ── Drag-to-move ─────────────────────────────────────────────
    onDragStart(ev) {
        // ignore clicks on buttons inside the header
        if (ev.target.closest("button") || ev.target.closest("a")) return;
        const el = this.windowRef.el;
        if (!el) return;

        const rect = el.getBoundingClientRect();
        this._drag.active = true;
        this._drag.startX = ev.clientX;
        this._drag.startY = ev.clientY;
        this._drag.origLeft = rect.left;
        this._drag.origTop = rect.top;

        // switch from bottom/right anchoring to top/left so movement is natural
        el.style.right = "auto";
        el.style.bottom = "auto";
        el.style.left = rect.left + "px";
        el.style.top = rect.top + "px";

        this.state.dragging = true;
        document.addEventListener("mousemove", this._onDragMove);
        document.addEventListener("mouseup", this._onDragEnd);
        ev.preventDefault();
    }

    _onDragMove(ev) {
        if (!this._drag.active) return;
        const el = this.windowRef.el;
        if (!el) return;
        const dx = ev.clientX - this._drag.startX;
        const dy = ev.clientY - this._drag.startY;
        // clamp inside viewport
        const maxLeft = window.innerWidth - el.offsetWidth;
        const maxTop = window.innerHeight - el.offsetHeight;
        el.style.left = Math.min(Math.max(0, this._drag.origLeft + dx), maxLeft) + "px";
        el.style.top = Math.min(Math.max(0, this._drag.origTop + dy), maxTop) + "px";
    }

    _onDragEnd() {
        this._drag.active = false;
        this.state.dragging = false;
        document.removeEventListener("mousemove", this._onDragMove);
        document.removeEventListener("mouseup", this._onDragEnd);
    }

    async _loadMessages(showLoading = true) {
        if (showLoading) this.state.loading = true;
        try {
            const LIMIT = 50;
            // Try live fetch from WAHA API first
            let result = await rpc("/waha/chat/messages/live", {
                chat_id: this.props.chatId,
                session_id: this.props.sessionId,
                limit: LIMIT,
            });
            // Fall back to stored messages if live fetch failed or returned empty
            if (!result.success || result.messages.length === 0) {
                result = await rpc("/waha/chat/messages", {
                    chat_id: this.props.chatId,
                    session_id: this.props.sessionId,
                    limit: LIMIT,
                    offset: 0,
                });
                this.state.isFallback = true;
            } else {
                this.state.isFallback = false;
            }
            if (result.success) {
                // WAHA returns newest-first; reverse to oldest-first for display
                const msgs = this.state.isFallback
                    ? result.messages
                    : [...result.messages].reverse();
                const wasAtBottom = this._initialLoad || this._isAtBottom();
                this.state.messages = msgs;
                this.state.hasMore = result.messages.length === LIMIT;
                if (wasAtBottom) {
                    setTimeout(() => this._scrollToBottom(), 50);
                }
                this._initialLoad = false;
            } else {
                this.notification.add(result.error || _t("Failed to load messages"), { type: "danger" });
            }
        } catch (e) {
            this.notification.add(_t("Failed to load messages"), { type: "danger" });
        } finally {
            if (showLoading) this.state.loading = false;
        }
    }

    async loadMoreMessages() {
        if (this.state.loadingMore) return;
        this.state.loadingMore = true;
        try {
            const LIMIT = 50;
            const el = this.messagesRef.el;
            const prevScrollHeight = el ? el.scrollHeight : 0;

            let result;
            if (this.state.isFallback) {
                result = await rpc("/waha/chat/messages", {
                    chat_id: this.props.chatId,
                    session_id: this.props.sessionId,
                    limit: LIMIT,
                    offset: this.state.messages.length,
                });
                if (result.success) {
                    this.state.messages = [...result.messages, ...this.state.messages];
                    this.state.hasMore = result.messages.length === LIMIT;
                }
            } else {
                result = await rpc("/waha/chat/messages/live", {
                    chat_id: this.props.chatId,
                    session_id: this.props.sessionId,
                    limit: LIMIT,
                    offset: this.state.messages.length,
                });
                if (result.success) {
                    const older = [...result.messages].reverse();
                    this.state.messages = [...older, ...this.state.messages];
                    this.state.hasMore = result.messages.length === LIMIT;
                }
            }
            // Keep scroll position after prepending
            if (el) {
                setTimeout(() => {
                    el.scrollTop = el.scrollHeight - prevScrollHeight;
                }, 50);
            }
        } catch (_e) {
            // silent
        } finally {
            this.state.loadingMore = false;
        }
    }

    async _pollMessages() {
        // In live mode the 30s full-refresh handles updates; polling stored messages
        // would send a WAHA string id as last_id which breaks the integer DB query.
        if (!this.state.isFallback) return;
        try {
            const lastId = this.state.messages.length
                ? this.state.messages[this.state.messages.length - 1].id
                : 0;
            const result = await rpc("/waha/chat/messages", {
                chat_id: this.props.chatId,
                session_id: this.props.sessionId,
                last_id: lastId,
            });
            if (result.success && result.messages.length > 0) {
                const wasAtBottom = this._isAtBottom();
                this.state.messages = [...this.state.messages, ...result.messages];
                if (wasAtBottom) {
                    setTimeout(() => this._scrollToBottom(), 50);
                }
            }
        } catch (_e) {
            // Silent fail on poll
        }
    }

    toggleMinimize() {
        this.state.minimized = !this.state.minimized;
        if (!this.state.minimized) {
            setTimeout(() => this._scrollToBottom(), 50);
        }
    }

    _isAtBottom() {
        const el = this.messagesRef.el;
        if (!el) return true;
        return el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    }

    _scrollToBottom() {
        const el = this.messagesRef.el;
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    }

    onClickAttach() {
        this.fileInputRef.el.click();
    }

    onFileSelected(ev) {
        const file = ev.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            const dataUrl = e.target.result;
            // dataUrl = "data:<mimetype>;base64,<data>"
            const base64 = dataUrl.split(",")[1];
            this.state.attachFile = {
                name: file.name,
                mimetype: file.type || 'application/octet-stream',
                data: base64,
                previewUrl: file.type.startsWith('image/') ? dataUrl : null,
                size: file.size,
            };
        };
        reader.readAsDataURL(file);
        // Reset so same file can be re-selected
        ev.target.value = "";
    }

    clearAttachment() {
        this.state.attachFile = null;
    }

    async onSend() {
        const text = this.state.text.trim();
        const attach = this.state.attachFile;
        if (!text && !attach) return;
        if (this.state.sending) return;

        this.state.sending = true;
        try {
            const params = {
                chat_id: this.props.chatId,
                session_id: this.props.sessionId,
                text: text,
                phone_number: this.props.phoneNumber,
                partner_id: this.props.partnerId || false,
            };

            if (attach) {
                params.file_data = attach.data;
                params.file_name = attach.name;
                params.file_mimetype = attach.mimetype;
            }

            const result = await rpc("/waha/chat/send", params);

            if (result.success) {
                this.state.messages = [...this.state.messages, result.message];
                this.state.text = "";
                this.state.attachFile = null;
                setTimeout(() => this._scrollToBottom(), 50);
            } else {
                this.notification.add(result.error || _t("Failed to send message"), { type: "danger" });
            }
        } catch (e) {
            this.notification.add(_t("Failed to send message"), { type: "danger" });
        } finally {
            this.state.sending = false;
        }
    }

    onKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.onSend();
        }
    }

    formatFileSize(bytes) {
        if (!bytes) return "";
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    formatTime(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    formatDate(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        return d.toLocaleDateString();
    }

    formatDateTime(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr.replace(" ", "T") + "Z");
        return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    }

    getStatusIcon(status) {
        const icons = {
            sent: "✓",
            delivered: "✓✓",
            read: "✓✓",
            failed: "✗",
            draft: "…",
        };
        return icons[status] || "";
    }

    isReadStatus(status) {
        return status === "read";
    }
}

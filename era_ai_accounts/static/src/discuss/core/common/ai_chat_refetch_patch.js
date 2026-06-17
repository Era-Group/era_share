import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

/**
 * Make the Ask AI reply appear without a manual page refresh.
 *
 * The AI answer reaches the browser through ONE path only: the mail bus
 * notification ``discuss.channel/new_message``. With this module's CLI proxy,
 * that push is emitted in a single burst at the very end of a long, fully
 * synchronous ``/ai/generate_response`` request (host-wide CLI lock + subprocess,
 * up to a few minutes) — a window during which the channel sees no other bus
 * traffic. If the websocket is interrupted at any point in that window (a reverse
 * proxy idle/read timeout being the usual culprit), the one-and-only push is
 * missed and the reply just sits in the database until a reload re-fetches it
 * over HTTP. Core never recovers from this for channels: ``fetchNewMessages()``
 * deliberately no-ops for an already-loaded ``discuss.channel`` (it trusts the
 * bus), so nothing pulls the message in.
 *
 * The era ``/ai/generate_response`` controller posts (and commits) the reply
 * BEFORE returning, so by the time the rpc in core's ``Thread.post`` settles the
 * message is guaranteed to exist server-side. We hook right after that and force
 * a "fetch newer messages" that bypasses the channel guard. This is idempotent:
 * if the bus already delivered the reply, the fetch finds nothing new.
 */
patch(Thread.prototype, {
    async post(body, postData = {}, extraData = {}) {
        const isAiChat = this.channel_type === "ai_chat";
        try {
            return await super.post(body, postData, extraData);
        } finally {
            // super.post (the ai module's override) has already awaited
            // /ai/generate_response and cleared the typing indicator.
            if (isAiChat) {
                this._eraFetchAiReply();
            }
        }
    },

    /**
     * Force-fetch messages newer than the newest persistent one, even for a
     * loaded discuss.channel. Mirrors core ``fetchNewMessages`` feeding logic
     * (minus the channel early-return) and never throws — delivery must never
     * break message posting.
     */
    async _eraFetchAiReply() {
        try {
            if (this.status === "loading") {
                return;
            }
            const after = this.isLoaded ? this.newestPersistentMessage?.id : undefined;
            const fetched = await this.fetchMessages({ after });
            let startIndex;
            if (after === undefined) {
                startIndex = 0;
            } else {
                const afterIndex = this.messages.findIndex((message) => message.id === after);
                if (afterIndex === -1) {
                    // The list shifted during the fetch; abort to avoid holes.
                    return;
                }
                startIndex = afterIndex + 1;
            }
            const known = new Set(this.messages.map((message) => message.id));
            const filtered = fetched.filter(
                (message) =>
                    !known.has(message.id) &&
                    (this.persistentMessages.length === 0 ||
                        message.id < this.oldestPersistentMessage.id ||
                        message.id > this.newestPersistentMessage.id)
            );
            if (filtered.length) {
                this.messages.splice(startIndex, 0, ...filtered);
            }
        } catch {
            // Best-effort: the bus may still deliver; do not surface errors.
        }
    },
});

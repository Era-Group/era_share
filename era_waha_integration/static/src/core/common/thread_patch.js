import { Thread } from "@mail/core/common/thread";
import { patch } from "@web/core/utils/patch";

// WAHA channels: display messages ordered by their real WhatsApp date (then id as a
// stable tiebreaker), instead of Discuss's default id order (= insertion order). Discuss
// gives a message imported now a high id, so a message recovered after an outage would
// otherwise land at the bottom even though its timestamp is older; sorting by date puts it
// back in its correct chronological place. Only the DISPLAY order changes here —
// fetching/pagination still work by id — so the only trade-off is minor rough edges around
// the id-based "newest" marker on very long conversations, acceptable for WAHA channels.
patch(Thread.prototype, {
    get orderedMessages() {
        const thread = this.props.thread;
        if (!thread?.is_waha_channel) {
            return super.orderedMessages;
        }
        const messages = this.state.mountedAndLoaded
            ? thread.messages
            : thread.phantomMessages;
        const asc = [...messages].sort((a, b) => {
            const ta = a.datetime?.ts ?? a.id;
            const tb = b.datetime?.ts ?? b.id;
            return ta === tb ? a.id - b.id : ta - tb;
        });
        return this.props.order === "asc" ? asc : asc.reverse();
    },
});

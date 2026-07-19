import { Message } from "@mail/core/common/message";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// WhatsApp-style delivery/read ticks for outbound WhatsApp messages:
//   sent → single grey ✓, delivered → double grey ✓✓, read → double blue ✓✓, error → red !
const OUTBOUND_STATUSES = ["outgoing", "sent", "delivered", "read", "replied", "error", "bounced", "cancel"];

patch(Message.prototype, {
    get isWahaOutboundStatus() {
        return OUTBOUND_STATUSES.includes(this.message.whatsappStatus);
    },
    get wahaTickMarks() {
        const s = this.message.whatsappStatus;
        if (["error", "bounced", "cancel"].includes(s)) {
            return "!";
        }
        if (["delivered", "read", "replied", "received"].includes(s)) {
            return "✓✓";
        }
        return "✓"; // outgoing / sent
    },
    get wahaTickClass() {
        const s = this.message.whatsappStatus;
        if (s === "read" || s === "replied") {
            return "o-waha-tick-read";
        }
        if (["error", "bounced", "cancel"].includes(s)) {
            return "text-danger";
        }
        return "text-muted";
    },
    // Failed-post tooltip: show the WAHA block reason when present (set by
    // Store.doMessagePost), else the standard generic text.
    get wahaPendingTitle() {
        return this.message.wahaBlockReason || _t("Failed to post the message. Click to retry");
    },
});

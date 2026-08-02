import { registerComposerAction } from "@mail/core/common/composer_actions";
import { Composer } from "@mail/core/common/composer";
import { Message as MessageRecord } from "@mail/core/common/message_model";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useState } from "@odoo/owl";

// Internal notes are available on every WhatsApp channel (WAHA and official Meta
// accounts alike): a note, or a mention of a colleague, must never reach the customer.
patch(Composer.prototype, {
    get postData() {
        const postData = super.postData;
        if (
            this.props.composer.targetThread?.channel_type === "whatsapp" &&
            this.props.composer.wahaInternalNote
        ) {
            postData.wahaInternalNote = true;
            this.props.composer.wahaInternalNote = false;
        }
        return postData;
    },
});

patch(MessageRecord.prototype, {
    get bubbleColor() {
        if (this.thread?.channel_type === "whatsapp" && this.isNote) {
            return "orange";
        }
        return super.bubbleColor;
    },
});

registerComposerAction("waha-internal-note", {
    btnClass: ({ composer }) =>
        composer.wahaInternalNote ? "o-sendMessageActive o-text-white shadow-sm" : "",
    condition: ({ composer }) => composer.targetThread?.channel_type === "whatsapp" && !composer.message,
    icon: "fa fa-lock",
    isActive: ({ composer }) => composer.wahaInternalNote,
    name: _t("Internal Note"),
    onSelected: ({ composer }) => {
        composer.wahaInternalNote = !composer.wahaInternalNote;
    },
    setup({ composer }) {
        if (composer.wahaInternalNote === undefined) {
            composer.wahaInternalNote = false;
        }
        useState(composer);
    },
    sequenceQuick: 29,
});

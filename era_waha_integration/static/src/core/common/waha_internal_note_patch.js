import { registerComposerAction } from "@mail/core/common/composer_actions";
import { Composer } from "@mail/core/common/composer";
import { Composer as ComposerRecord } from "@mail/core/common/composer_model";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { useState } from "@odoo/owl";

patch(ComposerRecord.prototype, {
    clear() {
        super.clear();
        this.wahaInternalNote = false;
    },
});

patch(Composer.prototype, {
    get postData() {
        const postData = super.postData;
        if (this.props.composer.wahaInternalNote) {
            postData.wahaInternalNote = true;
        }
        return postData;
    },
});

registerComposerAction("waha-internal-note", {
    btnClass: ({ composer }) =>
        composer.wahaInternalNote ? "o-sendMessageActive o-text-white shadow-sm" : "",
    condition: ({ composer }) => composer.targetThread?.is_waha_channel && !composer.message,
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

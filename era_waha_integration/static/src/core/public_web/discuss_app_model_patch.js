import { fields } from "@mail/core/common/record";
import { DiscussApp } from "@mail/core/public_web/discuss_app_model";

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(DiscussApp.prototype, {
    setup(env) {
        super.setup(...arguments);
        // A dedicated sidebar category so WAHA conversations are grouped separately
        // from official (Meta) WhatsApp channels, even though both use channel_type
        // 'whatsapp' under the hood.
        this.waha = fields.One("DiscussAppCategory", {
            compute() {
                return {
                    addTitle: _t("Search WAHA Channel"),
                    extraClass: "o-mail-DiscussSidebarCategory-whatsapp",
                    hideWhenEmpty: true,
                    icon: "fa fa-whatsapp",
                    id: "waha",
                    name: this.store.wahaCategoryName || _t("WAHA"),
                    sequence: 21,
                    serverStateKey: "is_discuss_sidebar_category_waha_open",
                };
            },
            eager: true,
        });
    },
});

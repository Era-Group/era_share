import { Store } from "@mail/core/common/store_service";
import { patch } from "@web/core/utils/patch";

patch(Store.prototype, {
    setup() {
        super.setup(...arguments);
        // Label for the WAHA sidebar category — set from the WhatsApp account name
        // by res.users._init_store_data (global store value).
        this.wahaCategoryName = "";
    },
});

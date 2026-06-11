/** @odoo-module **/
import { Component, useState, onMounted, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const QUEUE_STATUSES = new Set([
    "queued", "queue", "pending", "in_queue", "in queue",
    "processing", "in_progress", "in progress",
]);

function isQueueStatus(val) {
    if (!val) return false;
    const lower = val.trim().toLowerCase();
    if (QUEUE_STATUSES.has(lower)) return true;
    return lower.startsWith("queued:") || lower.startsWith("queue:");
}

export class EraspyCrmStatusField extends Component {
    static template = "era_spy_crm.EraspyCrmStatusField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.state = useState({ polling: false });
        this._intervalId = null;

        onMounted(() => this._startPollingIfNeeded());
        onWillUpdateProps((nextProps) => {
            const newVal = nextProps.record.data[nextProps.name] || "";
            if (isQueueStatus(newVal)) {
                this._startPollingIfNeeded();
            } else {
                this._stopPolling();
            }
        });
        onWillUnmount(() => this._stopPolling());
    }

    get value() {
        return this.props.record.data[this.props.name] || "";
    }

    get isQueued() {
        return isQueueStatus(this.value);
    }

    get isFailed() {
        const v = this.value.toLowerCase();
        return v.includes("failed") || v.includes("error") || v.includes("timed out") || v.includes("not_found") || v.includes("not found");
    }

    get badgeClass() {
        if (this.isFailed) return "text-bg-danger";
        if (this.value) return "text-bg-success";
        return "text-bg-secondary";
    }

    _startPollingIfNeeded() {
        if (this.isQueued && !this._intervalId) {
            this.state.polling = true;
            this._intervalId = setInterval(() => this._poll(), 3000);
        }
    }

    _stopPolling() {
        if (this._intervalId) {
            clearInterval(this._intervalId);
            this._intervalId = null;
        }
        this.state.polling = false;
    }

    async _poll() {
        try {
            const results = await this.orm.read(
                "crm.lead",
                [this.props.record.resId],
                ["eraspy_last_status"],
            );
            if (results && results[0]) {
                const newStatus = results[0].eraspy_last_status || "";
                if (!isQueueStatus(newStatus)) {
                    this._stopPolling();
                    await this.props.record.model.load();
                }
            }
        } catch {
            // silently ignore polling errors
        }
    }
}

export const eraspyCrmStatusField = {
    component: EraspyCrmStatusField,
    displayName: "Era Enrich CRM Status",
    supportedTypes: ["char"],
    extractProps: () => ({}),
};

registry.category("fields").add("eraspy_crm_status", eraspyCrmStatusField);

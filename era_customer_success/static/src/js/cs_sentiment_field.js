/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

const MOOD = { positive: "🙂", neutral: "😐", negative: "🙁" };
const BADGE = {
    positive: "text-bg-success",
    neutral: "text-bg-warning",
    negative: "text-bg-danger",
};

// Sentiment badge that reveals the per-ticket analysis breakdown on hover
// (sourced from the read-only computed `sentiment_detail` text field).
export class CsSentimentBadge extends Component {
    static template = "era_customer_success.CsSentimentBadge";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name];
    }
    get emoji() {
        return MOOD[this.value] || "";
    }
    get badgeClass() {
        return BADGE[this.value] || "text-bg-secondary";
    }
    get text() {
        const field = this.props.record.fields[this.props.name];
        const sel = (field.selection || []).find(([v]) => v === this.value);
        return sel ? sel[1] : "—";
    }
    get detail() {
        return this.props.record.data.sentiment_detail || "";
    }
    get tooltipInfo() {
        return JSON.stringify({ text: this.detail });
    }
}

export const csSentimentBadge = {
    component: CsSentimentBadge,
    // Pull the breakdown text even when it is not placed in the view.
    fieldDependencies: [{ name: "sentiment_detail", type: "text" }],
};

registry.category("fields").add("cs_sentiment_badge", csSentimentBadge);

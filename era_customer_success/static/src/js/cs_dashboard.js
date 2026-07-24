/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

export class CsDashboard extends Component {
    static template = "era_customer_success.CsDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            kpi: {
                accounts: 0, avgHealth: 0, atRisk: 0, mrr: 0,
                renewals: 0, avgCsat: 0, avgSentiment: 0, upsell: 0,
                openVoc: 0, highVoc: 0, lowEngagement: 0, criticalWallets: 0,
                overdueReviews: 0, dueWork: 0, qualifyingNeeds: 0,
            },
            engineers: [],
            atRisk: [],
        });
        onWillStart(() => this.loadData());
    }

    get atRiskDomain() {
        return ["|", ["churn_risk", "=", true], ["health_status", "in", ["at_risk", "critical"]]];
    }

    async loadData() {
        this.state.loading = true;
        const M = "cs.account";

        // Global aggregates (single group)
        const aggFields = [
            "health_score:avg", "mrr:sum", "upsell_revenue:sum",
            "csat_latest:avg", "sentiment_score:avg",
        ];
        const agg = await this.orm.call(M, "read_group", [[], aggFields, []], { lazy: false });
        const g = agg[0] || {};
        const k = this.state.kpi;
        k.accounts = g.__count || 0;
        k.avgHealth = Math.round(g.health_score || 0);
        k.mrr = Math.round(g.mrr || 0);
        k.upsell = Math.round(g.upsell_revenue || 0);
        k.avgCsat = Math.round((g.csat_latest || 0) * 10) / 10;
        k.avgSentiment = Math.round(g.sentiment_score || 0);
        k.atRisk = await this.orm.searchCount(M, this.atRiskDomain);
        k.renewals = await this.orm.searchCount(M, [["renewal_soon", "=", true]]);
        const today = new Date().toISOString().slice(0, 10);
        k.openVoc = await this.orm.searchCount("cs.voc.insight", [["state", "in", ["new", "triaged", "acted"]]]);
        k.highVoc = await this.orm.searchCount("cs.voc.insight", [["priority", "=", "high"], ["state", "in", ["new", "triaged"]]]);
        k.lowEngagement = await this.orm.searchCount(M, [["latest_adoption_status", "in", ["watch", "low"]]]);
        k.criticalWallets = await this.orm.searchCount("cs.support.wallet", [["status", "in", ["critical", "exhausted", "expired"]]]);
        k.overdueReviews = await this.orm.searchCount("cs.value.review", [["review_date", "<", today], ["state", "not in", ["closed", "cancelled"]]]);
        k.dueWork = await this.orm.searchCount("cs.weekly.suggestion", [["state", "=", "open"], ["due_date", "<=", today]]);
        k.qualifyingNeeds = await this.orm.searchCount("csm.offering", [["state", "in", ["draft", "presented"]]]);

        // Per-engineer leaderboard
        const rows = await this.orm.call(
            M, "read_group",
            [[["csm_user_id", "!=", false]],
             ["health_score:avg", "mrr:sum", "upsell_revenue:sum", "sentiment_score:avg"],
             ["csm_user_id"]],
            { lazy: false }
        );
        const eng = [];
        for (const r of rows) {
            const atRisk = await this.orm.searchCount(
                M, [["csm_user_id", "=", r.csm_user_id[0]], ...this.atRiskDomain]
            );
            eng.push({
                id: r.csm_user_id[0],
                name: r.csm_user_id[1],
                accounts: r.csm_user_id_count || r.__count || 0,
                health: Math.round(r.health_score || 0),
                mrr: Math.round(r.mrr || 0),
                upsell: Math.round(r.upsell_revenue || 0),
                sentiment: Math.round(r.sentiment_score || 0),
                atRisk,
            });
        }
        eng.sort((a, b) => b.health - a.health);
        this.state.engineers = eng;

        // At-risk accounts
        this.state.atRisk = await this.orm.searchRead(
            M, this.atRiskDomain,
            ["partner_id", "csm_user_id", "health_score", "health_status",
             "open_tickets_count", "sentiment_label", "days_to_renewal"],
            { limit: 15, order: "health_score asc" }
        );
        this.state.loading = false;
    }

    get tiles() {
        const k = this.state.kpi;
        return [
            { key: "accounts", icon: "fa-users", color: "primary", label: _t("Accounts"), value: k.accounts, domain: [] },
            { key: "health", icon: "fa-heartbeat", color: "success", label: "Avg Health", value: k.avgHealth, domain: [] },
            { key: "atRisk", icon: "fa-exclamation-triangle", color: "danger", label: "At Risk", value: k.atRisk, domain: this.atRiskDomain },
            { key: "renewals", icon: "fa-refresh", color: "warning", label: "Renewals < 90d", value: k.renewals, domain: [["renewal_soon", "=", true]] },
            { key: "mrr", icon: "fa-money", color: "info", label: "MRR", value: k.mrr, domain: [["mrr", ">", 0]] },
            { key: "csat", icon: "fa-star", color: "success", label: "Avg CSAT", value: k.avgCsat, domain: [] },
            { key: "sentiment", icon: "fa-smile-o", color: "primary", label: "Avg Sentiment", value: k.avgSentiment, domain: [] },
            { key: "upsell", icon: "fa-line-chart", color: "info", label: "Upsell Won", value: k.upsell, domain: [["upsell_revenue", ">", 0]] },
            { key: "highVoc", icon: "fa-bullhorn", color: "danger", label: _t("High Customer Voice"), value: k.highVoc, model: "cs.voc.insight", domain: [["priority", "=", "high"], ["state", "in", ["new", "triaged"]]] },
            { key: "lowEngagement", icon: "fa-plug", color: "warning", label: _t("Low Engagement"), value: k.lowEngagement, domain: [["latest_adoption_status", "in", ["watch", "low"]]] },
            { key: "criticalWallets", icon: "fa-hourglass-end", color: "danger", label: _t("Critical Support Hours"), value: k.criticalWallets, model: "cs.support.wallet", domain: [["status", "in", ["critical", "exhausted", "expired"]]] },
            { key: "overdueReviews", icon: "fa-calendar-times-o", color: "warning", label: _t("Overdue Value Reviews"), value: k.overdueReviews, model: "cs.value.review", domain: [["review_date", "<", new Date().toISOString().slice(0, 10)], ["state", "not in", ["closed", "cancelled"]]] },
            { key: "dueWork", icon: "fa-tasks", color: "primary", label: _t("Due Work"), value: k.dueWork, model: "cs.weekly.suggestion", domain: [["state", "=", "open"], ["due_date", "<=", new Date().toISOString().slice(0, 10)]] },
            { key: "qualifyingNeeds", icon: "fa-compass", color: "info", label: _t("Needs Being Validated"), value: k.qualifyingNeeds, model: "csm.offering", domain: [["state", "in", ["draft", "presented"]]] },
        ];
    }

    moodEmoji(label) {
        return { positive: "🙂", neutral: "😐", negative: "🙁" }[label] || "";
    }

    onTile(tile) {
        this.openRecords(tile.model || "cs.account", tile.label, tile.domain);
    }

    openRecords(model, name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window", name, res_model: model, domain: domain || [],
            views: [[false, "list"], [false, "form"]],
        });
    }

    openAccounts(name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Customer Success — " + name,
            res_model: "cs.account",
            domain: domain || [],
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
        });
    }

    openEngineer(eng) {
        this.openAccounts(eng.name, [["csm_user_id", "=", eng.id]]);
    }

    onTileKeydown(ev, tile) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.onTile(tile);
        }
    }

    onEngineerKeydown(ev, engineer) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.openEngineer(engineer);
        }
    }

    openAccount(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "cs.account",
            res_id: id,
            views: [[false, "form"]],
        });
    }
}

registry.category("actions").add("cs_dashboard", CsDashboard);

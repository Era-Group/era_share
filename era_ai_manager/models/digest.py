from odoo import api, fields, models


class Digest(models.Model):
    """Manager KPIs on the daily digest the owner already receives."""

    _inherit = "digest.digest"

    kpi_era_ai_attention = fields.Boolean("Customers Needing Attention")
    kpi_era_ai_attention_value = fields.Integer(compute="_compute_kpi_era_ai_values")
    kpi_era_ai_outreach = fields.Boolean("AI Messages Sent")
    kpi_era_ai_outreach_value = fields.Integer(compute="_compute_kpi_era_ai_values")
    kpi_era_ai_pending = fields.Boolean("Awaiting Your Approval")
    kpi_era_ai_pending_value = fields.Integer(compute="_compute_kpi_era_ai_values")
    # A broken agent is worth a line on the digest the owner already reads.
    # Without it, the only notice was a one-off alert email that may have
    # arrived a week ago and been scrolled past.
    kpi_era_ai_faults = fields.Boolean("Things Still Broken")
    kpi_era_ai_faults_value = fields.Integer(compute="_compute_kpi_era_ai_values")

    def _compute_kpi_era_ai_values(self):
        start, end, __ = self._get_kpi_compute_parameters()
        # Bound the period only when the digest supplied one. A bare
        # `sent_at >= False` is not "no filter" - it matches nothing.
        period = []
        if start:
            period.append(("sent_at", ">=", start))
        if end:
            period.append(("sent_at", "<", end))
        outreach = self.env["era.ai.outreach"]
        watchlists = self.env["era.ai.watchlist"].search([])
        attention = sum(watchlist.match_count for watchlist in watchlists)
        faults = self.env["era.ai.watchdog.alert"].search_count(
            [("state", "=", "open")])
        for digest in self:
            digest.kpi_era_ai_faults_value = faults
            digest.kpi_era_ai_attention_value = attention
            digest.kpi_era_ai_outreach_value = outreach.search_count(
                [("state", "=", "sent")] + period
            )
            digest.kpi_era_ai_pending_value = outreach.search_count(
                [("state", "=", "pending")]
            )

    def _compute_kpis_actions(self, company, user):
        response = super()._compute_kpis_actions(company, user)
        outreach_action = self.env.ref(
            "era_ai_manager.action_era_ai_outreach", raise_if_not_found=False
        )
        if outreach_action:
            link = "odoo/action-%s" % outreach_action.id
            response["kpi_era_ai_outreach"] = link
            response["kpi_era_ai_pending"] = link
        watchdog_action = self.env.ref(
            "era_ai_manager.action_era_ai_watchdog", raise_if_not_found=False
        )
        if watchdog_action:
            response["kpi_era_ai_faults"] = "odoo/action-%s" % watchdog_action.id
        watchlist_action = self.env.ref(
            "era_ai_manager.action_era_ai_watchlist", raise_if_not_found=False
        )
        if watchlist_action:
            response["kpi_era_ai_attention"] = "odoo/action-%s" % watchlist_action.id
        return response

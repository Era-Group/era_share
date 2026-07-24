from odoo import api, models


_SELF_SERVICE_MODELS = {
    'cs.account',
    'cs.account.copilot',
    'cs.adoption.assessment',
    'cs.call.briefing',
    'cs.capture.request',
    'cs.customer.import',
    'cs.followup.compose',
    'cs.next.action',
    'cs.service',
    'cs.service.recommendation.line',
    'cs.service.recommendation.wizard',
    'cs.stage',
    'cs.success.milestone',
    'cs.success.profile',
    'cs.success.stakeholder',
    'cs.suggestion.complete',
    'cs.support.wallet',
    'cs.value.review',
    'cs.voc.insight',
    'cs.weekly.suggestion',
    'csm.kpi.snapshot',
    'csm.offering',
}


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        descriptions = super().fields_get(allfields=allfields, attributes=attributes)
        if self._name not in _SELF_SERVICE_MODELS or (attributes and 'help' not in attributes):
            return descriptions

        for field_name, description in descriptions.items():
            if description.get('help'):
                continue
            field = self._fields[field_name]
            description['help'] = self._self_service_field_help(field, description)
        return descriptions

    @api.model
    def _self_service_field_help(self, field, description):
        label = description.get('string') or field.name.replace('_', ' ').title()
        name = field.name

        if name == 'state':
            return self.env._(
                "Shows the record's workflow stage. Use the action buttons in the header to move it forward; "
                "do not treat the status as a substitute for recording evidence and outcomes."
            )
        if name in {'status', 'health_status', 'latest_adoption_status', 'support_wallet_status'}:
            return self.env._(
                "Shows the current %(label)s calculated from the available customer evidence. Review the source "
                "metrics before deciding the next customer action.", label=label
            )
        if 'priority' in name or name in {'rank', 'attention_rank'}:
            return self.env._(
                "Shows how urgently %(label)s should be handled. Address higher priorities first, then verify the "
                "underlying reason before contacting the customer.", label=label
            )
        if name in {'score', 'confidence', 'recommendation_score', 'health_score', 'sentiment_score'}:
            return self.env._(
                "Shows the calculated %(label)s. Use it as a decision signal, not as proof on its own; review the "
                "supporting evidence and data confidence before acting.", label=label
            )
        if name in {'source', 'source_type', 'source_reference', 'source_key', 'source_res_id'}:
            return self.env._(
                "Identifies where this information came from. Use it to verify the original evidence and avoid "
                "duplicating a customer signal or assessment."
            )
        if name in {'evidence', 'recommendation_reason', 'reason', 'data_observations'}:
            return self.env._(
                "Records the evidence behind this conclusion. Keep it factual and specific so another CSM can "
                "verify the recommendation and continue the customer conversation."
            )
        if name in {'customer_need', 'potential_needs', 'blockers', 'risks_and_blockers', 'risks_or_blockers'}:
            return self.env._(
                "Document the customer's own need, risk, or blocker in concrete terms. Validate it with the "
                "customer before presenting a service or handing it to Sales."
            )
        if name in {'suitability_checked', 'customer_interest_confirmed'}:
            return self.env._(
                "Confirm this only after direct validation with the customer. It is a required qualification "
                "checkpoint and must not be inferred from an automated recommendation."
            )
        if name == 'need_timing':
            return self.env._(
                "Record when the customer expects to act on this need. Use the customer's stated timing to decide "
                "whether the need is qualified for a CRM opportunity."
            )
        if name == 'next_step':
            return self.env._(
                "Describe one clear next action with an owner and expected outcome. Add a date when follow-up is "
                "required; leave it empty only when no further action is needed."
            )
        if name == 'next_step_date':
            return self.env._(
                "Set the agreed date for the next step. Use a realistic customer commitment date so the action "
                "appears in the correct daily worklist."
            )
        if name.startswith('ai_') or 'generated' in name:
            return self.env._(
                "Shows AI assistance or when it was last generated. AI output is a reviewable draft or signal; "
                "verify facts, customer commitments, and commercial claims before use."
            )

        readonly = description.get('readonly') or field.compute or field.related
        if readonly:
            return self.env._(
                "Shows %(label)s from related records or system calculations. Review its source records when the "
                "value looks outdated; edit the source rather than this field.", label=label
            )
        if field.type == 'selection':
            return self.env._(
                "Choose the %(label)s that best matches the verified customer situation. This choice can affect "
                "workflow, prioritization, reporting, or the recommended next action.", label=label
            )
        if field.type == 'boolean':
            return self.env._(
                "Enable %(label)s only when the condition is verified. Review the related workflow and downstream "
                "automation before changing it.", label=label
            )
        if field.type in {'many2one', 'many2many', 'one2many'}:
            return self.env._(
                "Select or maintain %(label)s to keep this customer record connected to its supporting people, "
                "services, or evidence. Use only records belonging to the same customer and company.", label=label
            )
        if field.type in {'date', 'datetime'}:
            return self.env._(
                "Set %(label)s to the actual or agreed business date. Dates drive due work, reminders, review "
                "windows, and operational reporting.", label=label
            )
        if field.type in {'integer', 'float', 'monetary'}:
            return self.env._(
                "Enter %(label)s from a reliable source using the displayed unit. Accurate values improve health, "
                "adoption, prioritization, and reporting decisions.", label=label
            )
        return self.env._(
            "Record %(label)s clearly and factually so the next user understands the customer context and can take "
            "the correct next action without additional training.", label=label
        )

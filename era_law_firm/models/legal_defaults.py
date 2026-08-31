"""Stop asking the lawyer for things the file already knows.

Every required field with no default is a question the form puts to someone
mid-task. Some are real — a case needs a client — and some are the software
asking for its own bookkeeping: which stage a new case starts at, which
product an engagement bills through, when a hearing ends. Those are answered
here so the form asks only what the lawyer actually decides.

Defaults, not hidden writes: every one of these stays editable, and shows the
value it chose rather than filling it in on save.
"""
from datetime import timedelta

from odoo import api, fields, models

DEFAULT_HEARING_MINUTES = 60
SERVICE_PRODUCT_XMLID = 'era_law_firm.product_legal_services'


class LegalCase(models.Model):
    _inherit = 'legal.case'

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'stage_id' in fields_list and not values.get('stage_id'):
            # Every case starts at the beginning; asking is a formality.
            first = self.env['legal.case.stage'].search([], order='sequence, id', limit=1)
            if first:
                values['stage_id'] = first.id
        return values


class LegalEngagement(models.Model):
    _inherit = 'legal.engagement'

    @api.model
    def _default_service_product(self):
        """The product an engagement bills through.

        An accounting concept that a lawyer has no reason to hold an opinion
        about. The module ships one, and a firm that wants separate products
        per service can still choose.
        """
        # The xmlid names a product.template; engagements and expenses point
        # at the variant Odoo creates alongside it.
        template = self.env.ref(SERVICE_PRODUCT_XMLID, raise_if_not_found=False)
        if template and template.product_variant_id:
            return template.product_variant_id
        return self.env['product.product'].search(
            [('type', '=', 'service')], limit=1)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'product_id' in fields_list and not values.get('product_id'):
            product = self._default_service_product()
            if product:
                values['product_id'] = product.id
        return values


class LegalExpense(models.Model):
    _inherit = 'legal.expense'

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'product_id' in fields_list and not values.get('product_id'):
            product = self.env['legal.engagement']._default_service_product()
            if product:
                values['product_id'] = product.id
        if 'engagement_id' in fields_list and not values.get('engagement_id'):
            values.update(self._engagement_from_case(values.get('case_id')))
        return values

    @api.model
    def _engagement_from_case(self, case_id):
        """One active engagement is not a choice; it is the answer."""
        if not case_id:
            return {}
        engagements = self.env['legal.engagement'].search([
            ('case_id', '=', case_id), ('state', '=', 'active')])
        return {'engagement_id': engagements.id} if len(engagements) == 1 else {}

    @api.onchange('case_id')
    def _onchange_case_fills_engagement(self):
        if self.case_id and not self.engagement_id:
            found = self._engagement_from_case(self.case_id.id)
            if found:
                self.engagement_id = found['engagement_id']


class LegalTimeEntry(models.Model):
    _inherit = 'legal.time.entry'

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'engagement_id' in fields_list and not values.get('engagement_id'):
            values.update(self.env['legal.expense']._engagement_from_case(
                values.get('case_id')))
        return values

    @api.onchange('case_id')
    def _onchange_case_fills_engagement(self):
        if self.case_id and not self.engagement_id:
            found = self.env['legal.expense']._engagement_from_case(self.case_id.id)
            if found:
                self.engagement_id = found['engagement_id']
                self._onchange_engagement_sets_rate()

    @api.onchange('engagement_id')
    def _onchange_engagement_sets_rate(self):
        """Bill the hours at the rate the engagement agreed.

        `rate` was a free field nothing filled. A lawyer logged four hours,
        marked them billable, invoiced them — and the line came out at zero,
        because the number the client agreed to was sitting on the engagement
        and nobody carried it across. The invoice looked complete and the firm
        billed nothing.
        """
        if self.engagement_id and not self.rate:
            self.rate = self.engagement_id.hourly_rate

    @api.model_create_multi
    def create(self, vals_list):
        """The onchange covers the form; imports and the API come through here."""
        engagements = self.env['legal.engagement'].browse([
            values.get('engagement_id') for values in vals_list
            if values.get('engagement_id') and not values.get('rate')])
        rates = {engagement.id: engagement.hourly_rate for engagement in engagements}
        for values in vals_list:
            if not values.get('rate') and values.get('engagement_id') in rates:
                values['rate'] = rates[values['engagement_id']]
        return super().create(vals_list)


class LegalHearing(models.Model):
    _inherit = 'legal.hearing'

    @api.onchange('case_id')
    def _onchange_case_fills_hearing(self):
        """The case knows its own lawyer and what it is called."""
        if not self.case_id:
            return
        if not self.lawyer_id:
            self.lawyer_id = self.case_id.lawyer_id or self.env.user
        if not self.name:
            self.name = _hearing_name(self.env, self.case_id)

    @api.onchange('start_datetime')
    def _onchange_start_sets_end(self):
        """A lawyer knows when a hearing is called, rarely when it will end.

        An hour is a placeholder that is right often enough and obvious to
        change; demanding an exact end time before the hearing has happened is
        asking for a guess and storing it as fact.
        """
        if self.start_datetime and (
                not self.stop_datetime or self.stop_datetime <= self.start_datetime):
            self.stop_datetime = self.start_datetime + timedelta(
                minutes=DEFAULT_HEARING_MINUTES)


class LegalDeadline(models.Model):
    _inherit = 'legal.deadline'

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if 'user_id' in fields_list and not values.get('user_id'):
            values['user_id'] = self.env.user.id
        if 'source' in fields_list and not values.get('source'):
            values['source'] = self.env._('Entered by hand')
        return values

    @api.onchange('case_id')
    def _onchange_case_fills_owner(self):
        if self.case_id and self.case_id.lawyer_id:
            self.user_id = self.case_id.lawyer_id


def _hearing_name(env, case):
    return env._('Hearing — %(case)s', case=case.display_name)

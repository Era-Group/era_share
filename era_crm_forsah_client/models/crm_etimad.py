# -*- coding: utf-8 -*-
import logging

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ETIMAD_DATA_URL = 'https://service.era.net.sa/etimad/data'


class CrmEtimad(models.Model):
    _name = "crm.etimad.client"
    _description = "Etimad Tender"
    _inherit = ['crm.tender.ai.match.mixin']
    _order = "limit asc, id desc"

    @api.model
    def _ai_match_candidates_domain(self):
        # Only score open tenders (deadline not passed); skip the expired backlog.
        return [('active', '=', True), ('limit', '>=', fields.Date.today())]

    name = fields.Char("Name", required=True, readonly=True)
    category = fields.Char("Category")
    link = fields.Char("Link", readonly=True)
    company = fields.Char("Company", readonly=True)
    method = fields.Char("Method", readonly=True)
    ref = fields.Char("Ref", readonly=True, index=True)
    cost = fields.Char("Cost", readonly=True)
    questions = fields.Date('Questions', readonly=True)
    limit = fields.Date('Limit', readonly=True)
    publish = fields.Date('Publish', readonly=True)
    # NB: the relation table stays 'Tags' to preserve existing links; only the
    # user-facing label is fixed here (it previously defaulted to "Tag ids").
    tag_ids = fields.Many2many('crm.etimad.tag.client', 'Tags', string='Tags')
    etimad_id = fields.Char("Ref ID", readonly=True)
    lead_id = fields.Many2one(
        'crm.lead', string="Opportunity", readonly=True, copy=False,
        help="Opportunity created from this tender, if any.")
    study_state = fields.Selection(
        selection=[
            ('to_review', 'To Review'),
            ('studyable', 'Studyable'),
            ('rejected', 'Not Suitable'),
            ('converted', 'Converted'),
        ],
        string="Study Status", default='to_review', required=True,
        help="Triage status of the tender within the studyable-tenders pipeline.")
    active = fields.Boolean("Active", default=True)

    def action_archive_old(self):
        """Archive tenders whose submission deadline has passed."""
        today = fields.Date.today()
        self.search([('limit', '<', today), ('active', '=', True)]).write({'active': False})

    def action_mark_studyable(self):
        self.write({'study_state': 'studyable'})

    def action_mark_rejected(self):
        self.write({'study_state': 'rejected'})

    @staticmethod
    def _clean_date(value):
        """Coerce a feed date into something the Date field accepts.

        The Etimad API may send 'False' or '' for a missing date; anything
        that isn't a valid 'YYYY-MM-DD' value becomes False instead of crashing.
        """
        if not isinstance(value, str):
            return value if value else False
        value = value.strip()
        if not value or value == 'False':
            return False
        try:
            return fields.Date.to_date(value)
        except (ValueError, TypeError):
            return False

    def get_etimad_data(self):
        """Fetch the Etimad feed and create any tenders not already present."""
        try:
            response = requests.get(ETIMAD_DATA_URL, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as error:
            _logger.exception("Etimad API request failed")
            raise UserError(_("Etimad API request failed: %s", error))
        except ValueError as error:
            _logger.exception("Etimad API returned invalid JSON")
            raise UserError(_("Invalid JSON returned by the Etimad API: %s", error))

        if str(payload.get('code')) != '200':
            raise UserError(
                _("Unexpected response code from Etimad API: %s", payload.get('code')))

        for data in payload.get('data') or []:
            ref = data.get('ref')
            name = data.get('name')
            if not ref or not name:
                continue
            feed_vals = {
                'etimad_id': data.get('id'),
                'name': name,
                'link': data.get('link'),
                'category': data.get('category'),
                'limit': self._clean_date(data.get('limit')),
                'company': data.get('company'),
                'method': data.get('method'),
                'cost': data.get('cost'),
                'questions': self._clean_date(data.get('questions')),
                'publish': self._clean_date(data.get('publish')),
            }
            record = self.with_context(active_test=False).search([('ref', '=', ref)], limit=1)
            if record:
                # Update feed fields in place; triage, tags and lead are preserved.
                record.write(feed_vals)
            else:
                self.create({**feed_vals, 'ref': ref})
        return True

    @api.model
    def process_categories_to_tags(self, num_to_process=2):
        """Turn the dash-separated category string into tags.

        Processes at most *num_to_process* untagged records per call to keep
        the cron lightweight against a large backlog.
        """
        Tag = self.env['crm.etimad.tag.client'].sudo()
        cache = {tag.name: tag.id for tag in Tag.search([])}
        records = self.search([('tag_ids', '=', False), ('category', '!=', False)],
                              limit=num_to_process)
        for record in records:
            tag_ids = []
            for raw in record.category.split('-'):
                name = raw.strip()
                if not name:
                    continue
                tag_id = cache.get(name)
                if not tag_id:
                    tag_id = Tag.create({'name': name}).id
                    cache[name] = tag_id
                tag_ids.append(tag_id)
            if tag_ids:
                record.tag_ids = [(6, 0, tag_ids)]

    def etimad_open_link(self):
        self.ensure_one()
        if not self.link:
            raise UserError(_("This tender has no link to open."))
        return {
            'type': 'ir.actions.act_url',
            'url': self.link,
            'target': 'new',
        }

    def _get_source(self):
        return self.env['utm.source'].search([('name', '=', 'Etimad')], limit=1)

    def action_create_lead(self):
        """Create a CRM opportunity from this tender and link it back."""
        self.ensure_one()
        if self.lead_id:
            raise UserError(_("An opportunity has already been created for this tender."))

        source = self._get_source() or self.env['utm.source'].create({'name': 'Etimad'})
        lead = self.env['crm.lead'].create({
            'name': self.name,
            'description': self.category,
            'type': 'opportunity',
            'source_id': source.id,
            'referred': self.ref,
            'partner_name': self.company,
            'website': self.link,
            'date_open': self.publish,
            'date_deadline': self.limit,
        })
        lead.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Review Opportunity'),
            note=_('New Etimad opportunity requires review within 3 days.'),
            user_id=self.env.user.id,
        )
        self.write({'lead_id': lead.id, 'study_state': 'converted'})
        _logger.info("Created opportunity %s from Etimad tender %s", lead.id, self.name)
        return self.action_open_lead()

    def action_open_lead(self):
        self.ensure_one()
        if not self.lead_id:
            raise UserError(_("No opportunity is linked to this tender yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Opportunity"),
            'res_model': 'crm.lead',
            'res_id': self.lead_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

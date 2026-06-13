# -*- coding: utf-8 -*-
import logging
import re

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)

FORSAH_DATA_URL = 'https://service.era.net.sa/forsah/data'
# Map Arabic-Indic / Eastern-Arabic digits onto ASCII so numeric parsing works
# regardless of how the upstream feed renders the "days remaining" value.
_DIGIT_TRANS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


class CrmForsah(models.Model):
    _name = "crm.forsah.client"
    _description = "Forsah Studyable Tender"
    _inherit = ['mail.thread', 'mail.activity.mixin', 'crm.tender.ai.match.mixin']
    _order = "days_remaining asc, id desc"

    @api.model
    def _ai_match_candidates_domain(self):
        # Small list — score every active tender.
        return [('active', '=', True)]

    name = fields.Char(string="Name", required=True, readonly=True, tracking=True, index=True)
    category = fields.Char(string="Category", readonly=True, tracking=True)
    link = fields.Char(string="Link", readonly=True)
    size = fields.Char(string="Size", readonly=True, tracking=True)
    days = fields.Char(string="Days", readonly=True, tracking=True)
    days_remaining = fields.Integer(
        string="Days Remaining",
        compute="_compute_days_remaining",
        store=True,
        help="Number of days left to submit, parsed from the feed's days value. Used for sorting and filtering.",
    )
    city = fields.Char(string="City", readonly=True, tracking=True)
    tag_ids = fields.Many2many(
        'crm.forsah.tag.client',
        'crm_forsah_tag_rel',
        'forsah_id',
        'tag_id',
        string='Tags',
    )
    forsah_id = fields.Char(string="Ref ID", readonly=True, tracking=True, index=True)

    study_state = fields.Selection(
        selection=[
            ('to_review', 'To Review'),
            ('studyable', 'Studyable'),
            ('rejected', 'Not Suitable'),
            ('converted', 'Converted'),
        ],
        string="Study Status",
        default='to_review',
        required=True,
        tracking=True,
        index=True,
        help="Triage status of the tender within the studyable-tenders pipeline.",
    )
    lead_id = fields.Many2one(
        'crm.lead', string="Opportunity", readonly=True, copy=False, tracking=True,
        help="Opportunity created from this tender, if any.",
    )

    active = fields.Boolean(string="Active", default=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends('days')
    def _compute_days_remaining(self):
        for record in self:
            record.days_remaining = self._extract_int(record.days)

    @staticmethod
    def _extract_int(value):
        """Return the first integer found in *value* (0 if none)."""
        if not value:
            return 0
        digits = re.findall(r'\d+', str(value).translate(_DIGIT_TRANS))
        return int(digits[0]) if digits else 0

    # ------------------------------------------------------------------
    # Feed synchronisation
    # ------------------------------------------------------------------
    def get_forsah_data(self):
        """Fetch the Forsah feed and upsert it into ``crm.forsah.client``.

        Records are matched on ``forsah_id`` so that triage decisions
        (``study_state``), tags and any linked opportunity survive a refresh.
        Tenders that disappear from the feed are archived rather than deleted,
        preserving their history and any opportunities created from them.
        """
        try:
            response = requests.get(FORSAH_DATA_URL, timeout=30)
            response.raise_for_status()
            payload = response.json()
        except requests.exceptions.RequestException as error:
            _logger.exception("Forsah API request failed")
            raise UserError(_("Forsah API request failed: %s", error))
        except ValueError as error:
            _logger.exception("Forsah API returned invalid JSON")
            raise UserError(_("Invalid JSON returned by the Forsah API: %s", error))

        if str(payload.get('code')) != '200':
            raise UserError(
                _("Unexpected response code from Forsah API: %s", payload.get('code')))

        Tender = self.with_context(active_test=False)
        touched = self.browse()
        seen_refs = []
        for data in payload.get('data') or []:
            name = data.get('name')
            ref = data.get('id')
            if not name or not ref:
                # The feed always carries an id; skip malformed items rather than
                # creating an undeduplicated record on every run.
                if name and not ref:
                    _logger.warning("Forsah feed item without id skipped: %s", name)
                continue
            feed_vals = {
                'name': name,
                'link': data.get('link'),
                'size': data.get('size'),
                'category': data.get('category'),
                'days': data.get('days'),
                'city': data.get('city'),
            }
            record = Tender.search([('forsah_id', '=', ref)], limit=1)
            if record:
                # Refresh only feed-sourced fields; reactivate if it had been
                # archived for falling out of a previous feed.
                record.write({**feed_vals, 'active': True})
            else:
                record = self.create({**feed_vals, 'forsah_id': ref})
            touched |= record
            seen_refs.append(ref)

        touched._sync_category_tags()

        if seen_refs:
            stale = Tender.search([
                ('forsah_id', '!=', False),
                ('forsah_id', 'not in', seen_refs),
                ('active', '=', True),
            ])
            if stale:
                stale.write({'active': False})
        _logger.info("Forsah sync: %s tenders upserted.", len(touched))
        return True

    def _sync_category_tags(self):
        """Create/attach tags from each record's comma-separated category."""
        Tag = self.env['crm.forsah.tag.client'].sudo()
        cache = {tag.name: tag.id for tag in Tag.search([])}
        for record in self:
            if not record.category:
                continue
            tag_ids = []
            for raw in record.category.split(','):
                name = raw.strip()
                if not name:
                    continue
                tag_id = cache.get(name)
                if not tag_id:
                    tag_id = Tag.create({'name': name}).id
                    cache[name] = tag_id
                tag_ids.append(tag_id)
            record.tag_ids = [(6, 0, tag_ids)]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def forsah_open_link(self):
        self.ensure_one()
        if not self.link:
            raise UserError(_("This tender has no link to open."))
        return {
            'type': 'ir.actions.act_url',
            'url': self.link,
            'target': 'new',
        }

    def action_mark_studyable(self):
        self.write({'study_state': 'studyable'})

    def action_mark_rejected(self):
        self.write({'study_state': 'rejected'})

    def _get_source(self):
        return self.env['utm.source'].search([('name', '=', 'Forsah')], limit=1)

    def action_create_lead(self):
        """Create a CRM opportunity from this tender and link it back."""
        self.ensure_one()
        if self.lead_id:
            raise UserError(_("An opportunity has already been created for this tender."))

        Lead = self.env['crm.lead']
        source = self._get_source() or self.env['utm.source'].create({'name': 'Forsah'})
        description = _("Category: %(category)s | Days: %(days)s | Size: %(size)s",
                        category=self.category or '-', days=self.days or '-', size=self.size or '-')
        lead = Lead.create({
            'name': self.name,
            'description': description,
            'type': 'opportunity',
            'city': self.city,
            'website': self.link,
            'source_id': source.id,
            'company_id': self.company_id.id,
        })
        lead.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_('Review Opportunity'),
            note=_('New Forsah opportunity requires review within 3 days.'),
            date_deadline=fields.Date.context_today(self) + relativedelta(days=3),
            user_id=self.env.user.id,
        )
        self.write({'lead_id': lead.id, 'study_state': 'converted'})
        _logger.info("Created opportunity %s from Forsah tender %s", lead.id, self.name)
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

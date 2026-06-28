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
    due_date = fields.Date(
        string="Due Date", readonly=True, tracking=True,
        help="Absolute submission deadline. Days Remaining is derived from this so it stays correct as time passes.",
    )
    days_remaining = fields.Integer(
        string="Days Remaining",
        compute="_compute_days_remaining",
        store=True,
        help="Days left to submit, derived from the due date (refreshed daily). Used for sorting and filtering.",
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
    @api.depends('due_date', 'days')
    def _compute_days_remaining(self):
        today = fields.Date.context_today(self)
        for record in self:
            if record.due_date:
                record.days_remaining = (record.due_date - today).days
            else:
                record.days_remaining = self._extract_int(record.days)

    @api.model
    def _cron_refresh_days_remaining(self):
        """Recompute days_remaining from due_date so it keeps counting down as
        time passes, independent of when the feed last synced."""
        records = self.search([('active', '=', True), ('due_date', '!=', False)])
        records._compute_days_remaining()
        records.flush_recordset(['days_remaining'])
        return True

    @staticmethod
    def _parse_feed_date(value):
        """Coerce a feed date to a value the Date field accepts (False if bad)."""
        if not isinstance(value, str):
            return value if value else False
        value = value.strip()
        if not value or value == 'False':
            return False
        try:
            return fields.Date.to_date(value)
        except (ValueError, TypeError):
            return False

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

        Records are matched on ``link`` (the marketplace URL — a stable natural
        key) and updated in place, so triage decisions (``study_state``), tags
        and any linked opportunity always survive a refresh. Existing records
        are never deactivated by the sync; ones that drop out of the feed are
        left untouched (use due-date filters to hide expired ones).
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
        for data in payload.get('data') or []:
            name = data.get('name')
            link = data.get('link')
            if not name or not link:
                # `link` (the marketplace URL) is the stable natural key; skip
                # malformed items rather than creating an undeduplicated record.
                if name and not link:
                    _logger.warning("Forsah feed item without link skipped: %s", name)
                continue
            feed_vals = {
                'name': name,
                'link': link,
                'size': data.get('size'),
                'category': data.get('category'),
                'days': data.get('days'),
                'due_date': self._parse_feed_date(data.get('due_date')),
                'city': data.get('city'),
            }
            record = Tender.search([('link', '=', link)], limit=1)
            if record:
                # Update feed-sourced fields in place; never recreate, so triage
                # decisions, tags and any linked opportunity are preserved.
                record.write(feed_vals)
            else:
                record = self.create({**feed_vals, 'forsah_id': data.get('id')})
            touched |= record

        touched._sync_category_tags()
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

    @api.model
    def action_archive_old(self):
        """Archive opportunities whose submission deadline (due_date) has passed.

        Only genuinely expired records are archived — records merely absent from
        a feed refresh are left untouched.
        """
        today = fields.Date.today()
        expired = self.search([('due_date', '<', today), ('active', '=', True)])
        if expired:
            expired.write({'active': False})
        return True

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

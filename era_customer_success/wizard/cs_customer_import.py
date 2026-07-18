# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..models.cs_account import _cs_extract_json

_logger = logging.getLogger(__name__)

VALID_TIERS = ('strategic', 'growth', 'core', 'long_tail')


class CsCustomerImport(models.TransientModel):
    _name = 'cs.customer.import'
    _description = 'Smart Customer Import'

    source = fields.Selection([
        ('both', 'Tickets or Projects'),
        ('tickets', 'Tickets only'),
        ('projects', 'Projects only'),
    ], default='both', required=True, string='Bring customers having')
    use_ai_tier = fields.Boolean(
        string='Classify tier with AI', default=False,
        help="Ask the AI agent to classify each customer's tier from its engagement "
             "(falls back to a rule-based tier if AI is unavailable).")
    assign_engineers = fields.Boolean(
        string='Auto-assign to CSM engineers', default=True,
        help="Distribute the imported customers evenly (least-loaded engineer first) "
             "across users that have the Customer Success Engineer (CSM) permission.")
    limit = fields.Integer(string='Max customers', default=200)
    candidate_count = fields.Integer(string='Candidates found', compute='_compute_candidate_count')

    # ------------------------------------------------------------------
    def _candidate_partners(self):
        self.ensure_one()
        partner_ids = set()
        if self.source in ('both', 'tickets') and 'helpdesk.ticket' in self.env.registry:
            for grp in self.env['helpdesk.ticket'].sudo()._read_group(
                    [('partner_id', '!=', False)], ['partner_id'], []):
                partner_ids.add(grp[0].id)
        if self.source in ('both', 'projects') and 'project.project' in self.env.registry:
            for grp in self.env['project.project'].sudo()._read_group(
                    [('partner_id', '!=', False)], ['partner_id'], []):
                partner_ids.add(grp[0].id)
        partners = self.env['res.partner'].browse(list(partner_ids)).mapped('commercial_partner_id')
        # Exclude archived (inactive) companies — never import a customer that was archived.
        partners = partners.filtered(lambda p: p.is_company and p.active)
        managed = self.env['cs.account'].sudo().with_context(active_test=False).search([]).mapped('partner_id')
        return partners - managed

    @api.depends('source')
    def _compute_candidate_count(self):
        for wiz in self:
            wiz.candidate_count = len(wiz._candidate_partners()) if wiz.source else 0

    # ------------------------------------------------------------------
    def _engagement(self, partner):
        pids = self.env['res.partner'].search([('id', 'child_of', partner.id)]).ids
        tickets = self.env['helpdesk.ticket'].sudo().search_count([('partner_id', 'in', pids)]) \
            if 'helpdesk.ticket' in self.env.registry else 0
        projects = self.env['project.project'].sudo().search_count([('partner_id', 'in', pids)]) \
            if 'project.project' in self.env.registry else 0
        return tickets, projects

    def _heuristic_tier(self, partner):
        tickets, projects = self._engagement(partner)
        score = tickets + projects * 3
        if score >= 20:
            return 'strategic'
        if score >= 8:
            return 'growth'
        if score >= 2:
            return 'core'
        return 'long_tail'

    def _ai_classify_tiers(self, partners):
        """One batch AI call classifying candidates into tiers. Returns {partner_id: tier}."""
        agent = self.env.ref('era_customer_success.cs_customer_classify_agent', raise_if_not_found=False)
        if not agent:
            return {}
        plist = list(partners)[:60]
        lines = []
        for idx, p in enumerate(plist):
            tickets, projects = self._engagement(p)
            lines.append("%s | name: %s | tickets: %s | projects: %s | industry: %s" % (
                idx, p.name, tickets, projects, p.industry_id.name if p.industry_id else '-'))
        prompt = "Classify each of these customers into a tier based on engagement:\n" + "\n".join(lines)
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(prompt=prompt)
            data = _cs_extract_json(response[0] if response else '')
        except Exception:
            _logger.exception('AI customer classification failed')
            return {}
        if isinstance(data, dict) and 'customers' in data:
            data = data['customers']
        result = {}
        if isinstance(data, list):
            for item in data:
                try:
                    idx = int(item.get('index'))
                    tier = (item.get('tier') or '').lower()
                    if 0 <= idx < len(plist) and tier in VALID_TIERS:
                        result[plist[idx].id] = tier
                except (TypeError, ValueError, AttributeError):
                    continue
        return result

    # ------------------------------------------------------------------
    def action_import(self):
        self.ensure_one()
        partners = self._candidate_partners()
        if not partners:
            raise UserError(_('No new customers with ticket/project history were found.'))
        partners = partners[:max(1, self.limit)]

        tiers = self._ai_classify_tiers(partners) if self.use_ai_tier else {}

        engineer_ids = []
        load = {}
        if self.assign_engineers:
            engineer_ids = self.env['res.users'].search([
                ('group_ids', 'in', self.env.ref('era_customer_success.group_era_cs_user').id),
                ('active', '=', True),
                ('share', '=', False),
            ]).ids
            # Load-balance across engineers (least-loaded first) rather than random,
            # which could leave some engineers with zero. Seed from current portfolios.
            load = {uid: 0 for uid in engineer_ids}
            for user, count in self.env['cs.account'].sudo()._read_group(
                    [('csm_user_id', 'in', engineer_ids)], ['csm_user_id'], ['__count']):
                load[user.id] = count

        Account = self.env['cs.account']
        created = Account
        skipped = 0
        for partner in partners:
            # never duplicate: skip any customer already in the list
            if Account.with_context(active_test=False).search_count([('partner_id', '=', partner.id)]):
                skipped += 1
                continue
            vals = {
                'partner_id': partner.id,
                'tier': tiers.get(partner.id) or self._heuristic_tier(partner),
            }
            if engineer_ids:
                uid = min(load, key=load.get)   # least-loaded engineer
                vals['csm_user_id'] = uid
                load[uid] += 1
            created |= Account.create(vals)
        if not created:
            raise UserError(_('All matching customers are already in the list — nothing to import.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Imported Customers (%s)', len(created)),
            'res_model': 'cs.account',
            'view_mode': 'list,kanban,form',
            'domain': [('id', 'in', created.ids)],
            'context': {'search_default_group_csm': 1},
        }

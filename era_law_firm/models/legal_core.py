import hashlib
import re
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


class LegalCaseStage(models.Model):
    _name = 'legal.case.stage'
    _description = 'Legal Case Stage'
    _order = 'sequence, id'
    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    fold = fields.Boolean()
    is_closed = fields.Boolean()
    is_rejected = fields.Boolean()
    color = fields.Integer()
    company_id = fields.Many2one('res.company', index=True)


class LegalCase(models.Model):
    _name = 'legal.case'
    _description = 'Legal Case'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'portal.mixin']
    _check_company_auto = True
    _order = 'id desc'

    name = fields.Char(default='New', copy=False, readonly=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company, index=True)
    najiz_number = fields.Char(index=True, tracking=True)
    case_type = fields.Selection([('litigation','Litigation'),('execution','Execution'),('consultation','Consultation'),('other','Other')], tracking=True)
    jurisdiction = fields.Char(string='Jurisdiction (legacy text)')
    court = fields.Char(string='Court (legacy text)')
    circuit = fields.Char(string='Circuit (legacy text)')
    city = fields.Char(default=lambda self: self.env.company.legal_default_city)
    najiz_url = fields.Char()
    client_id = fields.Many2one('res.partner', required=True, check_company=True, tracking=True)
    lawyer_id = fields.Many2one('res.users', tracking=True)
    team_user_ids = fields.Many2many('res.users', 'legal_case_user_rel', string='Case Team')
    party_ids = fields.One2many('legal.case.party', 'case_id')
    stage_id = fields.Many2one('legal.case.stage', required=True, tracking=True, domain="['|',('company_id','=',False),('company_id','=',company_id)]")
    state = fields.Selection([('draft','Draft'),('confirmed','Confirmed'),('closed','Closed'),('cancelled','Cancelled')], default='draft', tracking=True)
    confidential = fields.Boolean(default=True)
    internal_notes = fields.Html(groups='era_law_firm.group_legal_manager,era_law_firm.group_legal_lawyer')
    claim_amount = fields.Monetary()
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', store=True)
    conflict_check_id = fields.Many2one('legal.conflict.check', copy=False)
    hearing_ids = fields.One2many('legal.hearing', 'case_id')
    deadline_ids = fields.One2many('legal.deadline', 'case_id')
    document_ids = fields.One2many('legal.document', 'case_id')
    next_hearing_id = fields.Many2one('legal.hearing', compute='_compute_next_hearing_id')
    access_url = fields.Char(compute='_compute_access_url')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                company = self.env['res.company'].browse(vals.get('company_id')) or self.env.company
                code = f'legal.case.{company.id}'
                seq = self.env['ir.sequence'].sudo().search([('code','=',code),('company_id','=',company.id)], limit=1)
                if not seq:
                    seq = self.env['ir.sequence'].sudo().create({'name': f'Legal Case {company.name}', 'code': code, 'prefix': 'LAW/%(year)s/', 'padding': 5, 'company_id': company.id})
                vals['name'] = seq.next_by_id()
        return super().create(vals_list)

    @api.depends('hearing_ids.start_datetime', 'hearing_ids.state')
    def _compute_next_hearing_id(self):
        now = fields.Datetime.now()
        for rec in self:
            rec.next_hearing_id = rec.hearing_ids.filtered(lambda h: h.state != 'cancelled' and h.start_datetime >= now).sorted('start_datetime')[:1]

    def _compute_access_url(self):
        for rec in self:
            rec.access_url = f'/my/legal-cases/{rec.id}'

    def action_confirm(self):
        for rec in self:
            if not all((rec.client_id, rec.lawyer_id, rec.case_type, rec.stage_id)):
                raise UserError(_('Client, lawyer, case type and stage are required.'))
            if not rec.conflict_check_id or rec.conflict_check_id.state not in ('clear','overridden') or rec.conflict_check_id.party_signature != rec._party_signature():
                raise UserError(_('Run a valid conflict check after the latest party change.'))
            rec.state = 'confirmed'
            rec.message_post(body=_('Case confirmed by %s on %s') % (self.env.user.name, fields.Datetime.now()))

    def action_close(self):
        for rec in self:
            if any(a.available_balance for a in self.env['legal.trust.account'].search([('partner_id','=',rec.client_id.id),('company_id','=',rec.company_id.id)])):
                raise UserError(_('Settle the client trust balance before closing the case.'))
            rec.state = 'closed'
            rec.message_post(body=_('Case closed by %s') % self.env.user.name)

    def action_reopen(self): self.write({'state':'confirmed'})
    def action_cancel(self): self.write({'state':'cancelled'})
    def unlink(self):
        if any(r.state != 'draft' for r in self): raise UserError(_('Only draft cases can be deleted.'))
        return super().unlink()

    def _party_signature(self):
        """A fingerprint of who is on the file, safe to store and compare.

        Two constraints shape it. Identity numbers are readable by legal
        managers only, yet the comparison needs them — so they are read with
        sudo, as system logic that uses the number without showing it. And the
        signature is stored on the conflict check, which a lawyer can read, so
        the raw values must not survive into it: storing them plainly would
        hand every restricted identity number to anyone who can open a check.
        It is hashed; equality is all it is ever used for.
        """
        self.ensure_one()
        values = [('client', self.client_id)] + [(line.role, line.partner_id) for line in self.party_ids]
        normalized = []
        for role, partner in values:
            partner = partner.sudo()
            identity = re.sub(r'\D', '', partner.legal_identity_number or '')
            registration = re.sub(r'\D', '', partner.legal_registration_number or '')
            phone = re.sub(r'\D', '', partner.phone or '')[-9:]
            normalized.append('|'.join((role or '', re.sub(r'\s+', ' ', (partner.name or '').strip().lower()), identity, registration, phone, (partner.email or '').strip().lower())))
        return hashlib.sha256('\n'.join(sorted(normalized)).encode()).hexdigest()

    def init(self):
        self.env.cr.execute('ALTER TABLE legal_case DROP CONSTRAINT IF EXISTS legal_case_najiz_unique')
        self.env.cr.execute('DROP INDEX IF EXISTS legal_case_najiz_nonempty_unique')
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS legal_case_najiz_nonempty_unique ON legal_case (company_id, btrim(najiz_number)) WHERE najiz_number IS NOT NULL AND btrim(najiz_number) <> ''")


class LegalCaseParty(models.Model):
    _name = 'legal.case.party'
    _description = 'Legal Case Party'
    _check_company_auto = True
    case_id = fields.Many2one('legal.case', required=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='case_id.company_id', store=True, index=True)
    partner_id = fields.Many2one('res.partner', required=True, check_company=True)
    role = fields.Selection([('client','Client'),('opponent','Opponent'),('representative','Representative'),('other','Other')], required=True)
    representative_id = fields.Many2one('res.partner')
    lawyer_id = fields.Many2one('res.users')
    portal_visible = fields.Boolean()

    @api.constrains('partner_id','role','case_id')
    def _check_roles(self):
        for rec in self:
            if rec.role == 'opponent' and rec.partner_id == rec.case_id.client_id:
                raise ValidationError(_('The client cannot also be an opponent.'))


class LegalConflictCheck(models.Model):
    _name = 'legal.conflict.check'
    _description = 'Conflict Check'
    case_id = fields.Many2one('legal.case', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='case_id.company_id', store=True, index=True)
    state = fields.Selection([('draft','Draft'),('clear','Clear'),('blocked','Blocked'),('overridden','Overridden')], default='draft')
    party_signature = fields.Char()
    line_ids = fields.One2many('legal.conflict.check.line','check_id')
    override_reason = fields.Text()
    approved_by = fields.Many2one('res.users')
    approved_at = fields.Datetime()

    def action_run_check(self):
        """Look for the same person, not merely the same record.

        Matching on partner_id alone means a client entered twice — «محمد
        عبدالله» and «محمد عبد الله» are two res.partner rows — passes the
        check and the firm is told there is no conflict. That is a
        professional-liability failure, not a data-quality annoyance, and
        duplicate contact records are the normal state of any client list.

        Three keys, strongest first: the same partner record; the same
        national ID or commercial registration; the same name once Arabic
        orthography is normalised. Each match records which key found it, so a
        lawyer reviewing a hit can see whether it is certain or a name
        coincidence to judge.
        """
        Party = self.env['legal.case.party']
        for rec in self:
            # The findings are the system's record, not the lawyer's: a lawyer
            # may run the check (ACL gives read-only on the lines), and sudo
            # here is what lets the run record what it found — while keeping
            # the lines untouchable by hand, which is the point of them.
            rec = rec.sudo()
            rec.line_ids.unlink()
            partners = rec.case_id.client_id | rec.case_id.party_ids.mapped('partner_id')
            # sudo, deliberately: record rules show a lawyer their own book,
            # and a conflict check that only compares against what the current
            # user can see is blind to the firm-wide conflicts it exists to
            # catch — another lawyer's client is exactly the case that matters.
            # What the hit reveals (partner, case reference, role) is the
            # minimum a conflicts register must show to be actionable.
            candidates = Party.sudo().search([
                ('company_id', '=', rec.company_id.id),
                ('case_id', '!=', rec.case_id.id),
                ('case_id.state', 'in', ('confirmed', 'closed')),
            ])
            # Other files' clients live on client_id, not necessarily in a party
            # row — and taking an engagement against a former client is the
            # classic conflict. Relying on someone having added the client as a
            # party row made the most important comparison optional.
            other_cases = self.env['legal.case'].sudo().search([
                ('company_id', '=', rec.company_id.id),
                ('id', '!=', rec.case_id.id),
                ('state', 'in', ('confirmed', 'closed')),
            ])
            # Two comparisons with different scopes. Party rows are compared
            # against everyone on the new file, as before. Other files' clients
            # are compared against the new file's OPPOSING side only: acting
            # against a former client is the classic conflict, while acting for
            # the same client on a second matter is a Tuesday — comparing their
            # client against our client would block every returning client.
            opposing = rec.case_id.party_ids.filtered(
                lambda party: party.role != 'client').mapped('partner_id')
            pool = [(c.partner_id, c.case_id, c.role, partners) for c in candidates]
            pool += [(c.client_id, c, 'client', opposing) for c in other_cases if c.client_id]
            hits, seen = [], set()
            for partner, source_case, role, against in pool:
                key = (partner.id, source_case.id)
                if key in seen:
                    continue
                basis = rec._conflict_basis(against, partner)
                if basis:
                    seen.add(key)
                    hits.append((0, 0, {
                        'partner_id': partner.id,
                        'source_case_id': source_case.id,
                        'role': role,
                        'match_basis': basis,
                    }))
            rec.write({
                'party_signature': rec.case_id._party_signature(),
                'state': 'blocked' if hits else 'clear',
                'line_ids': hits,
            })
            rec.case_id.conflict_check_id = rec

    @api.model
    def _identity_keys(self, partner):
        """Identity numbers reduced to digits and letters, so formatting differs harmlessly."""
        keys = set()
        # sudo: the comparison needs the number, the user never sees it — the
        # match reports 'same ID number' as its basis, not the number itself.
        partner = partner.sudo()
        for value in (partner.legal_identity_number, partner.legal_registration_number):
            cleaned = re.sub(r'[^0-9a-zA-Z]', '', value or '')
            if len(cleaned) >= 6:  # too short to identify anyone
                keys.add(cleaned)
        return keys

    def _conflict_basis(self, partners, other):
        """Why this partner counts as one of ours, or False."""
        self.ensure_one()
        if not other:
            return False
        if other in partners:
            return 'same_partner'
        ours = set()
        for partner in partners:
            ours |= self._identity_keys(partner)
        if ours & self._identity_keys(other):
            return 'identity_number'
        names = {self._name_key(p.name) for p in partners if p.name}
        names.discard('')
        if other.name and self._name_key(other.name) in names:
            return 'normalised_name'
        return False

    @api.model
    def _name_key(self, name):
        """A name reduced to what identifies the person rather than the typing.

        Spaces come out as well as the orthography the shared normaliser
        handles: «عبدالله» and «عبد الله» are one name written two ways, and
        that split is the most common variant in Saudi records. Done here
        rather than in normalize_legal_text, which other comparisons rely on
        keeping word boundaries.
        """
        return self._normalize_arabic_text(name).replace(' ', '')

    def action_manager_override(self):
        self.ensure_one()
        if not self.env.user.has_group('era_law_firm.group_legal_manager') or not self.override_reason:
            raise UserError(_('A manager and a mandatory reason are required.'))
        self.write({'state':'overridden','approved_by':self.env.user.id,'approved_at':fields.Datetime.now()})
        self.case_id.message_post(body=_('Conflict overridden by %s. Reason: %s') % (self.env.user.name, self.override_reason))


class LegalConflictCheckLine(models.Model):
    _name = 'legal.conflict.check.line'
    _description = 'Conflict Check Result'
    check_id = fields.Many2one('legal.conflict.check', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True)
    source_case_id = fields.Many2one('legal.case', required=True)
    role = fields.Char()
    # A name match and an ID match are not the same evidence, and the lawyer
    # deciding whether to override needs to know which one this is.
    match_basis = fields.Selection([
        ('same_partner', 'Same contact record'),
        ('identity_number', 'Same ID / registration number'),
        ('normalised_name', 'Same name (different record)'),
    ], string='Matched on', default='same_partner')


class LegalHearing(models.Model):
    _name = 'legal.hearing'
    _description = 'Legal Hearing'
    _inherit = ['mail.thread','mail.activity.mixin']
    _check_company_auto = True
    name = fields.Char(required=True)
    case_id = fields.Many2one('legal.case', required=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='case_id.company_id', store=True, index=True)
    start_datetime = fields.Datetime(required=True)
    stop_datetime = fields.Datetime(required=True)
    hijri_date = fields.Char()
    lawyer_id = fields.Many2one('res.users', required=True)
    state = fields.Selection([('draft','Draft'),('confirmed','Confirmed'),('done','Done'),('cancelled','Cancelled')], default='draft')
    calendar_event_id = fields.Many2one('calendar.event', copy=False, ondelete='set null')
    reminder_scheduled = fields.Boolean(copy=False)

    @api.constrains('start_datetime','stop_datetime')
    def _check_dates(self):
        for r in self:
            if r.stop_datetime <= r.start_datetime: raise ValidationError(_('End must be after start.'))

    def action_confirm(self):
        for r in self:
            vals={'name':f'{r.case_id.name} - {r.name}','start':r.start_datetime,'stop':r.stop_datetime,'user_id':r.lawyer_id.id,'legal_hearing_id':r.id}
            r.calendar_event_id = r.calendar_event_id.write(vals) and r.calendar_event_id or self.env['calendar.event'].create(vals)
            r.state='confirmed'
    def action_done(self): self.write({'state':'done'})
    def action_cancel(self):
        self.mapped('calendar_event_id').unlink(); self.write({'state':'cancelled','calendar_event_id':False})
    def unlink(self):
        if any(r.state != 'draft' for r in self): raise UserError(_('Only draft hearings can be deleted.'))
        self.mapped('calendar_event_id').unlink(); return super().unlink()
    @api.model
    def _cron_reminders(self):
        # The window is per company: the setting exists so a firm can choose it, and
        # the job runs across every firm on the database.
        now = fields.Datetime.now()
        for company in self.env['res.company'].sudo().search([]):
            limit = fields.Datetime.add(now, days=company.legal_hearing_reminder_days or 1)
            for r in self.search([('state','=','confirmed'),('reminder_scheduled','=',False),
                                  ('company_id','=',company.id),('start_datetime','<=',limit)]):
                r.activity_schedule('mail.mail_activity_data_todo', user_id=r.lawyer_id.id, summary=_('Upcoming legal hearing')); r.reminder_scheduled=True


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'
    legal_hearing_id = fields.Many2one('legal.hearing', index=True, ondelete='set null')


class LegalDeadline(models.Model):
    _name = 'legal.deadline'
    _description = 'Legal Deadline'
    _inherit = ['mail.thread','mail.activity.mixin']
    _check_company_auto = True
    name=fields.Char(required=True)
    case_id=fields.Many2one('legal.case',required=True,ondelete='cascade',check_company=True)
    company_id=fields.Many2one(related='case_id.company_id',store=True,index=True)
    deadline_date=fields.Date(required=True)
    source=fields.Char(required=True)
    user_id=fields.Many2one('res.users',required=True)
    state=fields.Selection([('draft','Draft'),('confirmed','Confirmed'),('done','Done'),('cancelled','Cancelled')],default='draft')
    reminder_scheduled=fields.Boolean(copy=False)
    def action_confirm(self): self.write({'state':'confirmed'})
    def action_done(self): self.write({'state':'done'})
    def action_cancel(self): self.write({'state':'cancelled'})
    @api.model
    def _cron_reminders(self):
        # Same defect as hearings had, and worse here: a missed statutory deadline is
        # not a missed meeting, so it gets its own longer window.
        today = fields.Date.today()
        for company in self.env['res.company'].sudo().search([]):
            limit = fields.Date.add(today, days=company.legal_deadline_reminder_days or 1)
            for r in self.search([('state','=','confirmed'),('reminder_scheduled','=',False),
                                  ('company_id','=',company.id),('deadline_date','<=',limit)]):
                r.activity_schedule('mail.mail_activity_data_todo',user_id=r.user_id.id,summary=_('Upcoming legal deadline')); r.reminder_scheduled=True


class LegalDocument(models.Model):
    _name='legal.document'; _description='Legal Document'; _inherit=['mail.thread','mail.activity.mixin']; _check_company_auto=True
    name=fields.Char(required=True); case_id=fields.Many2one('legal.case',required=True,ondelete='cascade',check_company=True)
    company_id=fields.Many2one(related='case_id.company_id',store=True,index=True); document_type=fields.Selection([('pleading','Pleading'),('contract','Contract'),('judgment','Judgment'),('deed','Deed'),('poa','Power of Attorney'),('other','Other')],default='other')
    version=fields.Char(default='1.0'); restricted=fields.Boolean(); allowed_user_ids=fields.Many2many('res.users'); owner_id=fields.Many2one('res.users',default=lambda s:s.env.user,required=True)
    reviewer_id=fields.Many2one('res.users'); state=fields.Selection([('draft','Draft'),('review','In Review'),('approved','Approved'),('rejected','Rejected'),('archived','Archived')],default='draft')
    attachment_id=fields.Many2one('ir.attachment',required=True,ondelete='restrict'); portal_published=fields.Boolean(); najiz_reference=fields.Char(); hijri_date=fields.Char(); expiry_date=fields.Date()
    def action_submit_review(self): self.write({'state':'review'})
    def action_approve(self):
        for r in self:
            if r.owner_id == self.env.user: raise UserError(_('The author cannot approve their own document.'))
            r.write({'state':'approved','reviewer_id':self.env.user.id})
    def action_reject(self): self.write({'state':'rejected','reviewer_id':self.env.user.id})
    def action_publish_portal(self):
        if any(r.state!='approved' for r in self): raise UserError(_('Only approved documents can be published.'))
        self.write({'portal_published':True})
    def action_unpublish_portal(self): self.write({'portal_published':False})
    def unlink(self):
        if any(r.state=='approved' for r in self): raise UserError(_('Approved documents must be archived.'))
        return super().unlink()


class ResPartner(models.Model):
    _inherit='res.partner'
    legal_identity_number=fields.Char(groups='era_law_firm.group_legal_manager')
    legal_registration_number=fields.Char(groups='era_law_firm.group_legal_manager')

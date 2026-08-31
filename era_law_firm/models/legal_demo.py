"""Test-data generator.

Odoo's own demo mechanism only fires on a database created with demo data, and
this one was not, so the same generator is exposed two ways: through the manifest
`demo` hook for a fresh demo database, and through a wizard under Configuration
for the rest of the time.

Everything it creates is registered under a dedicated pseudo-module so it can be
removed again in one go. Generation is seeded, so two runs on the same database
produce the same firm.
"""

import logging
import random

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEMO_MODULE = '__era_law_demo__'

# Two ready portal logins, created with the data and removed with it. The
# passwords are fixed and documented on the Load Test Data screen — this is a
# test tool for a staging database, which is also why DEPLOYMENT.md forbids
# loading demo data on production.
PORTAL_CLIENT_LOGIN = 'client.demo@example.sa'
PORTAL_OPPONENT_LOGIN = 'opponent.demo@example.sa'
PORTAL_DEMO_PASSWORD = 'PortalDemo!2026'

CLIENT_COMPANIES = [
    'شركة الراجحي للتجارة', 'مؤسسة الفيصل للمقاولات', 'شركة نجد للصناعات الغذائية',
    'مجموعة الخليج القابضة', 'شركة تبوك الزراعية', 'مؤسسة الحرمين للنقل',
    'شركة الشرق الأوسط للتأمين', 'مصنع الرياض للبلاستيك', 'شركة جدة للتطوير العقاري',
    'مؤسسة الدمام للمعدات', 'شركة عسير للاتصالات', 'مجموعة القصيم التجارية',
]
CLIENT_INDIVIDUALS = [
    'عبدالله بن محمد العتيبي', 'نورة بنت سعد القحطاني', 'فهد بن عبدالعزيز الدوسري',
    'منى بنت خالد الشهري', 'سعود بن ناصر الحربي', 'ريم بنت فيصل الغامدي',
    'ماجد بن سلطان الزهراني', 'هند بنت عمر المالكي',
]
OPPONENT_COMPANIES = [
    'شركة الأنوار للمقاولات', 'مؤسسة البناء الحديث', 'شركة المراعي الذهبية',
    'مجموعة السلام للاستثمار', 'شركة الوطنية للشحن', 'مؤسسة الرواد للتوريدات',
    'شركة المستقبل للتقنية', 'مصنع الجزيرة للحديد', 'شركة الساحل للخدمات',
    'مؤسسة الأمانة للصيانة', 'شركة النخبة للاستشارات', 'مجموعة الديار العقارية',
]
OPPONENT_INDIVIDUALS = [
    'صالح بن إبراهيم البقمي', 'عائشة بنت يوسف السبيعي', 'تركي بن حمد المطيري',
    'لطيفة بنت أحمد الرشيد', 'بندر بن مساعد العنزي', 'أمل بنت راشد الجهني',
    'وليد بن سامي الخالدي', 'سارة بنت طلال الشمري',
]
CITIES = ['الرياض', 'جدة', 'الدمام', 'مكة المكرمة', 'المدينة المنورة', 'الخبر', 'أبها', 'تبوك', 'بريدة']

CASE_SUBJECTS = {
    'litigation': [
        'مطالبة مالية عن عقد توريد', 'دعوى فسخ عقد إيجار', 'مطالبة بتعويض عن أضرار',
        'نزاع على ملكية عقار', 'دعوى مطالبة بأتعاب مقاولة', 'اعتراض على قرار إداري',
        'دعوى عمالية عن إنهاء تعسفي', 'نزاع شراكة بين شركاء', 'مطالبة بقيمة شيكات',
    ],
    'execution': [
        'تنفيذ حكم مطالبة مالية', 'تنفيذ سند لأمر', 'تنفيذ حكم إخلاء عقار',
        'تنفيذ محضر صلح', 'تنفيذ حكم نفقة',
    ],
    'consultation': [
        'مراجعة عقد شراكة', 'استشارة في نظام العمل', 'مراجعة عقد امتياز تجاري',
        'استشارة في تأسيس شركة', 'مراجعة اتفاقية عدم إفشاء',
    ],
    'other': ['توثيق وكالة شرعية', 'إنهاء إجراءات حصر إرث'],
}

DOCUMENT_TITLES = {
    'pleading': ['صحيفة الدعوى', 'مذكرة جوابية', 'مذكرة تعقيب'],
    'contract': ['عقد التوريد محل النزاع', 'عقد الإيجار'],
    'judgment': ['صك الحكم الابتدائي', 'حكم الاستئناف'],
    'deed': ['صك الملكية'],
    'poa': ['الوكالة الشرعية'],
    'other': ['كشف حساب', 'مراسلات بين الطرفين'],
}

DEADLINE_SUBJECTS = ['تقديم مذكرة جوابية', 'الاعتراض على الحكم', 'تقديم بينة', 'الرد على مذكرة الخصم']
HEARING_SUBJECTS = ['الجلسة الأولى', 'جلسة مرافعة', 'جلسة سماع بينة', 'جلسة نطق بالحكم', 'جلسة تبادل مذكرات']
TIME_NARRATIVES = ['دراسة الملف وإعداد المذكرة', 'حضور جلسة', 'مراجعة مستندات الخصم',
                   'إعداد لائحة اعتراض', 'اجتماع مع الموكل', 'بحث نظامي']
EXPENSE_NARRATIVES = ['رسوم قضائية', 'أتعاب خبير', 'رسوم تصوير مستندات', 'انتقالات', 'رسوم ترجمة معتمدة']

OVERRIDE_REASONS = [
    'الملف السابق منتهٍ ومختلف الموضوع، ولا صلة له بمحل النزاع الحالي.',
    'الطرف خصم في الملفين معاً ولم يسبق للمكتب تمثيله.',
    'حصل المكتب على تنازل خطي من الموكل السابق.',
    'التمثيل السابق اقتصر على إجراء توثيقي لا يمس موضوع الدعوى.',
]


class LegalDemoData(models.AbstractModel):
    _name = 'legal.demo.data'
    _description = 'Legal Test Data Generator'

    # ------------------------------------------------------------------
    # bookkeeping
    # ------------------------------------------------------------------

    @api.model
    def _tag(self, record, key):
        """Register the record under the demo pseudo-module so it can be purged."""
        self.env['ir.model.data'].create({
            'name': key, 'module': DEMO_MODULE,
            'model': record._name, 'res_id': record.id, 'noupdate': True,
        })
        return record

    @api.model
    def _existing_count(self):
        return self.env['ir.model.data'].search_count([
            ('module', '=', DEMO_MODULE), ('model', '=', 'legal.case')])

    # ------------------------------------------------------------------
    # generation
    # ------------------------------------------------------------------

    @api.model
    def _generate(self, case_count=50, seed=20260825):
        company = self.env.company
        if not self.env['account.account'].with_company(company).search_count([], limit=1):
            raise UserError(_('Install a chart of accounts before loading test data.'))
        company._setup_legal_trust_accounting()

        offset = self._existing_count()
        # Seeded by how much is already there, not by the seed alone: the load
        # screen offers a second batch on top of the first, and a bare seed
        # redraws the same Najiz numbers, which the uniqueness constraint
        # rejects. A fresh database still produces the same firm every time.
        rng = random.Random(seed + offset)

        product = self._demo_product()
        stages = self.env['legal.case.stage'].search([], order='sequence')
        courts = self.env['legal.court'].search([('degree', '=', 'first')])
        rules = self.env['legal.deadline.rule'].search([])
        lawyers = self._demo_lawyers()

        clients = self._demo_partners(CLIENT_COMPANIES, CLIENT_INDIVIDUALS, 'client', rng, offset)
        opponents = self._demo_partners(OPPONENT_COMPANIES, OPPONENT_INDIVIDUALS, 'opponent', rng, offset)

        cases = self.env['legal.case']
        for index in range(case_count):
            cases |= self._demo_case(index + offset, rng, clients, opponents, stages,
                                     courts, rules, lawyers, product, company)

        self._ensure_portal_accounts(cases)
        _logger.info('era_law_firm: generated %s test case(s), %s client(s), %s opponent(s)',
                     len(cases), len(clients), len(opponents))
        return cases

    @api.model
    def _demo_product(self):
        product = self.env.ref(f'{DEMO_MODULE}.product_legal_services', raise_if_not_found=False)
        if product:
            return product
        income = self.env['account.account'].with_company(self.env.company).search(
            [('account_type', '=', 'income')], limit=1)
        values = {'name': 'أتعاب محاماة', 'type': 'service'}
        if income:
            values['property_account_income_id'] = income.id
        return self._tag(self.env['product.product'].create(values), 'product_legal_services')

    @api.model
    def _demo_lawyers(self):
        lawyer_group = self.env.ref('era_law_firm.group_legal_lawyer')
        existing = self.env['res.users'].search([('group_ids', 'in', lawyer_group.id)])
        if len(existing) >= 3:
            return existing[:5]
        names = ['المحامي عبدالرحمن السالم', 'المحامية هيا الفهد', 'المحامي ياسر العمري']
        users = self.env['res.users'].browse()
        for position, name in enumerate(names):
            key = f'user_lawyer_{position}'
            found = self.env.ref(f'{DEMO_MODULE}.{key}', raise_if_not_found=False)
            users |= found or self._tag(self.env['res.users'].create({
                'name': name, 'login': f'demo_lawyer_{position}@era.net.sa',
                'company_id': self.env.company.id,
                'group_ids': [(4, lawyer_group.id)],
            }), key)
        return users | existing

    @api.model
    def _demo_partners(self, companies, individuals, kind, rng, offset):
        partners = self.env['res.partner'].browse()
        for position, name in enumerate(companies + individuals):
            key = f'partner_{kind}_{position}'
            found = self.env.ref(f'{DEMO_MODULE}.{key}', raise_if_not_found=False)
            if found:
                partners |= found
                continue
            is_company = position < len(companies)
            values = {
                'name': name,
                'is_company': is_company,
                'city': rng.choice(CITIES),
                'country_id': self.env.ref('base.sa').id,
                'phone': f'05{rng.randint(10000000, 59999999)}',
                'email': f'{kind}{position + offset}@example.sa',
            }
            partner = self.env['res.partner'].create(values)
            # identity numbers are manager-only, so they are written after creation
            partner.sudo().write({
                'legal_registration_number': str(rng.randint(1010000000, 1019999999)) if is_company else False,
                'legal_identity_number': str(rng.randint(1000000000, 1099999999)) if not is_company else False,
            })
            partners |= self._tag(partner, key)
        return partners

    @api.model
    def _demo_case(self, index, rng, clients, opponents, stages, courts, rules, lawyers, product, company):
        case_type = rng.choices(
            ['litigation', 'execution', 'consultation', 'other'], weights=[60, 20, 15, 5])[0]
        client = rng.choice(clients)
        opponent = rng.choice(opponents)
        lawyer = rng.choice(lawyers)
        court = rng.choice(courts) if courts else self.env['legal.court']
        filed = fields.Date.subtract(fields.Date.today(), days=rng.randint(20, 640))

        case = self._tag(self.env['legal.case'].create({
            'name': 'New',
            'client_id': client.id,
            'lawyer_id': lawyer.id,
            'team_user_ids': [(6, 0, rng.sample(lawyers.ids, min(2, len(lawyers))))],
            'case_type': case_type,
            'stage_id': rng.choice(stages).id if stages else False,
            'company_id': company.id,
            'jurisdiction_id': court.jurisdiction_id.id,
            'court_id': court.id,
            'city': court.city or rng.choice(CITIES),
            'najiz_number': str(rng.randint(4300000000, 4699999999)),
            'claim_amount': rng.choice([25000, 75000, 120000, 350000, 800000, 1500000]),
            'date_filed': filed,
            'confidential': rng.random() < 0.3,
        }), f'case_{index}')
        case.message_post(body=rng.choice(CASE_SUBJECTS[case_type]))

        self.env['legal.case.party'].create({
            'case_id': case.id, 'partner_id': opponent.id, 'role': 'opponent'})

        # A case only confirms after screening. Repeat opponents -- insurers, large
        # contractors -- block often in real practice, and a manager clears most of
        # those on the record, so the generator exercises that path too.
        case.action_run_conflict_check()
        check = case.conflict_check_id
        if check.state == 'blocked' and rng.random() < 0.75:
            # Fabricated history, not a user action: action_manager_override() asks who
            # is clicking, and the generator may be run by the installer or a script.
            # The end state and the chatter entry are written the same way it would.
            reason = rng.choice(OVERRIDE_REASONS)
            check.sudo().write({
                'state': 'overridden', 'override_reason': reason,
                'approved_by': lawyer.id, 'approved_at': fields.Datetime.now(),
            })
            case.message_post(body=_('Conflict overridden by %s. Reason: %s') % (lawyer.name, reason))
        if check.state in ('clear', 'overridden') and rng.random() < 0.9:
            case.action_confirm()

        self._demo_hearings(case, rng, filed)
        self._demo_deadlines(case, rng, rules, filed)
        self._demo_documents(case, rng, case_type)
        if case.state == 'confirmed':
            self._demo_billing(case, rng, product)
            self._demo_trust(case, rng)
        return case

    @api.model
    def _demo_hearings(self, case, rng, filed):
        if case.case_type == 'consultation':
            return
        for position in range(rng.randint(0, 4)):
            start = fields.Datetime.add(
                fields.Datetime.to_datetime(filed), days=rng.randint(15, 400), hours=rng.randint(8, 13))
            hearing = self.env['legal.hearing'].create({
                'name': rng.choice(HEARING_SUBJECTS),
                'case_id': case.id,
                'lawyer_id': case.lawyer_id.id,
                'start_datetime': start,
                'stop_datetime': fields.Datetime.add(start, hours=1),
                'hijri_date': f'{rng.randint(1, 29)}/{rng.randint(1, 12)}/144{rng.randint(5, 7)}هـ',
            })
            if start < fields.Datetime.now():
                hearing.action_confirm()
                hearing.action_done()
            elif rng.random() < 0.8:
                hearing.action_confirm()

    @api.model
    def _demo_deadlines(self, case, rng, rules, filed):
        for _position in range(rng.randint(0, 2)):
            rule = rng.choice(rules) if rules else self.env['legal.deadline.rule']
            start = fields.Date.add(filed, days=rng.randint(10, 300))
            deadline = self.env['legal.deadline'].create({
                'name': rng.choice(DEADLINE_SUBJECTS),
                'case_id': case.id,
                'user_id': case.lawyer_id.id,
                'rule_id': rule.id,
                'start_date': start,
                'deadline_date': fields.Date.add(start, days=rule.days or 30),
                'source': 'تبليغ بموعد الجلسة',
            })
            if rng.random() < 0.7:
                deadline.action_confirm()
            if deadline.deadline_date < fields.Date.today() and rng.random() < 0.6:
                deadline.action_done()

    @api.model
    def _demo_documents(self, case, rng, case_type):
        kinds = ['pleading', 'contract', 'poa'] if case_type != 'consultation' else ['contract']
        for kind in rng.sample(kinds, rng.randint(1, len(kinds))):
            title = rng.choice(DOCUMENT_TITLES[kind])
            document = self.env['legal.document'].create({
                'name': f'{title} - {case.name}',
                'case_id': case.id,
                'document_type': kind,
                'owner_id': case.lawyer_id.id,
                'file_data': self._demo_file(title, case.name),
                'file_name': f'{kind}.txt',
                'restricted': rng.random() < 0.15,
                'najiz_reference': str(rng.randint(100000, 999999)) if rng.random() < 0.4 else False,
            })
            if rng.random() < 0.7:
                document.action_submit_review()
                if rng.random() < 0.8:
                    document.action_approve()
                    if rng.random() < 0.5:
                        document.action_publish_portal()

    @api.model
    def _demo_file(self, title, reference):
        import base64
        body = f'{title}\nالقضية: {reference}\nمستند تجريبي لأغراض الاختبار فقط.\n'
        return base64.b64encode(body.encode('utf-8'))

    @api.model
    def _demo_billing(self, case, rng, product):
        engagement = self.env['legal.engagement'].create({
            'name': f'عقد أتعاب - {case.name}',
            'case_id': case.id,
            'billing_type': rng.choice(['hourly', 'fixed', 'hourly']),
            'hourly_rate': rng.choice([600, 800, 1000, 1500]),
            'amount': rng.choice([20000, 45000, 90000]),
            'product_id': product.id,
        })
        engagement.action_activate()

        entries = self.env['legal.time.entry']
        for _position in range(rng.randint(1, 6)):
            entry = self.env['legal.time.entry'].create({
                'name': rng.choice(TIME_NARRATIVES),
                'case_id': case.id,
                'engagement_id': engagement.id,
                'user_id': case.lawyer_id.id,
                'date': fields.Date.subtract(fields.Date.today(), days=rng.randint(1, 300)),
                'hours': rng.choice([1, 2, 3, 4, 6, 8]),
                'rate': engagement.hourly_rate,
            })
            if rng.random() < 0.75:
                entry.action_mark_billable()
                entries |= entry

        for _position in range(rng.randint(0, 2)):
            expense = self.env['legal.expense'].create({
                'name': rng.choice(EXPENSE_NARRATIVES),
                'case_id': case.id,
                'engagement_id': engagement.id,
                'product_id': product.id,
                'amount': rng.choice([500, 1200, 2500, 5000]),
            })
            if rng.random() < 0.7:
                expense.action_approve()

        if entries and rng.random() < 0.6:
            self._demo_invoice(case, engagement, entries)

    @api.model
    def _demo_invoice(self, case, engagement, entries):
        result = self.env['legal.invoice.create.wizard'].create({
            'case_id': case.id, 'engagement_id': engagement.id,
            'time_entry_ids': [(6, 0, entries.ids)],
        }).action_create_invoice()
        invoice = self.env['account.move'].browse(result['res_id'])
        invoice.write({'invoice_date': fields.Date.today()})
        try:
            invoice.action_post()
        except Exception as error:
            # e-invoicing clearance belongs to l10n_sa_edi; leave the invoice in draft
            # rather than pretending the firm is onboarded with ZATCA.
            _logger.info('era_law_firm: test invoice %s left in draft (%s)', invoice.name, error)
        return invoice

    @api.model
    def _demo_trust(self, case, rng):
        if rng.random() > 0.5:
            return
        account = self.env['legal.trust.account'].search([
            ('partner_id', '=', case.client_id.id), ('company_id', '=', case.company_id.id)], limit=1)
        if not account:
            account = self._tag(self.env['legal.trust.account'].create({
                'partner_id': case.client_id.id, 'company_id': case.company_id.id,
            }), f'trust_{case.client_id.id}')
        self.env['legal.trust.operation.wizard'].create({
            'trust_account_id': account.id,
            'case_id': case.id,
            'transaction_type': 'deposit',
            'amount': rng.choice([10000, 25000, 50000, 100000]),
            'reference': f'حوالة بنكية {rng.randint(100000, 999999)}',
        }).action_apply()

    # ------------------------------------------------------------------
    # removal
    # ------------------------------------------------------------------

    @api.model
    def _ensure_portal_accounts(self, cases):
        """Two logins that make the portal demonstrable out of the box.

        The client account belongs to the generated client with the most to
        look at; the opponent account exists to demonstrate the boundary —
        logging in as the other side of seven cases and seeing none of them
        is the security model, shown rather than asserted. Without these,
        whoever tests the portal grabs the first demo email they find, and
        the plausible-looking ones are the opponents.
        """
        Users = self.env['res.users'].sudo().with_context(no_reset_password=True)
        visible = cases.filtered(lambda c: c.state not in ('draft', 'cancelled'))
        if not visible:
            return

        def ensure(login, partner):
            existing = Users.search([('login', '=', login)], limit=1)
            if existing:
                # Someone already owns this login. Adopting it would mean
                # resetting a password that is not ours and deleting an account
                # we did not make; say so and leave it alone.
                _logger.warning(
                    'era_law_firm: portal demo login %s already exists; leaving it '
                    'untouched, so its own password applies, not %s.',
                    login, PORTAL_DEMO_PASSWORD)
                return existing
            user = Users.create({
                'name': partner.name, 'login': login,
                'password': PORTAL_DEMO_PASSWORD, 'partner_id': partner.id,
                'lang': 'ar_001',
                'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])]})
            self._tag(user, 'portal_user_%s' % login.split('@')[0].replace('.', '_'))
            return user

        richest = max(visible.mapped('client_id'), key=lambda partner: len(
            visible.filtered(lambda c: c.client_id == partner)))
        ensure(PORTAL_CLIENT_LOGIN, richest)

        opponents = visible.mapped('party_ids').filtered(
            lambda pr: pr.role == 'opponent').mapped('partner_id')
        # never hand the opponent login to someone who is also a client somewhere
        clients = visible.mapped('client_id')
        pure = (opponents - clients)
        if pure:
            ensure(PORTAL_OPPONENT_LOGIN, pure[0])

    def _purge(self):
        """Remove everything the generator made.

        Two things make this more than a bulk unlink. The module's own rules refuse
        to delete a confirmed case, a posted trust movement or an approved document
        -- correctly, since those protect the audit trail -- so the records are walked
        back to draft first, as an administrator clearing test data would do by hand.
        And several links into a case are restrict-on-delete (engagements, time,
        expenses, conflict citations), so children go before parents.
        """
        data = self.env['ir.model.data'].sudo().search([('module', '=', DEMO_MODULE)])
        if not data:
            return 0
        removed = len(data)

        # Snapshot the references now: deleting a record cascades its ir.model.data
        # row away, and a stale recordset would blow up halfway through.
        index = {}
        for item in data:
            index.setdefault(item.model, []).append(item.res_id)

        def tagged(model):
            return self.env[model].sudo().browse(index.get(model, [])).exists()

        # 0. the two portal logins go first: a res.users row restricts deleting
        #    its partner, and those partners are swept below as demo data.
        #    Only those two — the demo LAWYER users are tagged as well, and
        #    hearings reference them, so they wait for the sweep at the end.
        portal_logins = (PORTAL_CLIENT_LOGIN, PORTAL_OPPONENT_LOGIN)
        tagged('res.users').filtered(lambda user: user.login in portal_logins).unlink()

        cases = tagged('legal.case')
        engagements = self.env['legal.engagement'].sudo().search([('case_id', 'in', cases.ids)])

        # 1. billing sources let go of their invoice lines, then the invoices go
        entries = self.env['legal.time.entry'].sudo().search([('case_id', 'in', cases.ids)])
        expenses = self.env['legal.expense'].sudo().search([('case_id', 'in', cases.ids)])
        fees = self.env['legal.success.fee'].sudo().search([('engagement_id', 'in', engagements.ids)])
        milestones = self.env['legal.engagement.milestone'].sudo().search(
            [('engagement_id', 'in', engagements.ids)])
        for records in (entries, expenses, fees, milestones):
            records.write({'invoice_line_id': False})

        invoices = self.env['account.move'].sudo().search([('legal_case_id', 'in', cases.ids)])
        invoices.filtered(lambda move: move.state == 'posted').button_draft()
        invoices.filtered(lambda move: move.state != 'cancel').button_cancel()
        invoices.unlink()

        # 2. trust movements are reversed rather than erased, so they need cancelling first
        transactions = self.env['legal.trust.transaction'].sudo().search([
            '|', ('case_id', 'in', cases.ids), ('trust_account_id', 'in', tagged('legal.trust.account').ids)])
        accounts = transactions.trust_account_id | tagged('legal.trust.account')
        transactions.filtered(lambda tx: tx.state == 'posted').action_cancel()
        # Cancelling posts a reversal, so both entries are still on the books and would
        # hold the client partner hostage; the generator's journal entries go too.
        trust_moves = transactions.move_id | transactions.reversal_move_id
        transactions.write({'state': 'draft'})
        transactions.unlink()
        accounts.exists().write({'state': 'open'})

        trust_moves = trust_moves.exists()
        trust_moves.line_ids.filtered('reconciled').remove_move_reconcile()
        trust_moves.filtered(lambda move: move.state == 'posted').button_draft()
        trust_moves.filtered(lambda move: move.state != 'cancel').button_cancel()
        trust_moves.unlink()

        # 3. anything holding a restrict link to a case
        fees.unlink()
        milestones.unlink()
        entries.unlink()
        expenses.unlink()
        engagements.write({'state': 'draft'})
        engagements.unlink()
        self.env['legal.conflict.check.line'].sudo().search([
            ('source_case_id', 'in', cases.ids)]).unlink()
        self.env['legal.consultation'].sudo().search([('case_id', 'in', cases.ids)]).write(
            {'case_id': False, 'state': 'draft'})

        # 4. the cases themselves; parties, hearings, deadlines and documents cascade
        cases.hearing_ids.mapped('calendar_event_id').unlink()
        cases.hearing_ids.write({'state': 'draft'})
        cases.document_ids.write({'state': 'draft', 'portal_published': False})
        cases.write({'state': 'draft', 'conflict_check_id': False})
        cases.unlink()

        # Any journal entry still naming a generated partner would hold it back, including
        # entries left behind by an earlier run whose case link has since been dropped.
        partners = tagged('res.partner')
        if partners:
            # A trust entry carries the client on its lines, not on the header, so both
            # have to be looked at or the partner stays pinned by an invisible reference.
            stragglers = self.env['account.move'].sudo().search([
                '|', ('partner_id', 'in', partners.ids), ('line_ids.partner_id', 'in', partners.ids)])
            stragglers.filtered(lambda move: move.state == 'posted').button_draft()
            stragglers.filtered(lambda move: move.state != 'cancel').button_cancel()
            stragglers.unlink()

        for model in ('legal.trust.account', 'res.partner', 'res.users', 'product.product'):
            for record in tagged(model):
                try:
                    with self.env.cr.savepoint():
                        record.unlink()
                except Exception as error:
                    # Something outside the generator picked this record up. Leaving it
                    # is better than aborting the purge half-done.
                    _logger.info('era_law_firm: kept %s %s (%s)', model, record.id, error)

        self.env['ir.model.data'].sudo().search([('module', '=', DEMO_MODULE)]).unlink()
        _logger.info('era_law_firm: removed %s test record reference(s)', removed)
        return removed


class LegalDemoDataWizard(models.TransientModel):
    _name = 'legal.demo.data.wizard'
    _description = 'Load Legal Test Data'

    case_count = fields.Integer(
        string='Cases to create', default=50, required=True,
        help="How many cases to generate, together with their clients, opponents, hearings, deadlines, documents and billing.")
    existing_count = fields.Integer(
        string='Already loaded', compute='_compute_existing_count',
        help="Test cases already present in this database.")

    def _compute_existing_count(self):
        count = self.env['legal.demo.data']._existing_count()
        for record in self:
            record.existing_count = count

    def action_load(self):
        self.ensure_one()
        if self.case_count < 1:
            raise UserError(_('Enter how many cases to create.'))
        cases = self.env['legal.demo.data']._generate(self.case_count)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Test Cases'),
            'res_model': 'legal.case',
            'view_mode': 'list,form',
            'domain': [('id', 'in', cases.ids)],
        }

    def action_purge(self):
        self.ensure_one()
        self.env['legal.demo.data']._purge()
        return {'type': 'ir.actions.act_window_close'}

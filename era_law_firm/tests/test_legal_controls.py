from odoo import fields
from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError

@tagged('post_install','-at_install')
class TestLegalControls(TransactionCase):
    def test_arabic_normalization_and_deadline_rule(self):
        check=self.env['legal.conflict.check']
        self.assertEqual(check._normalize_arabic_text('  أَحْمــد  '),'احمد')
        with self.assertRaises(ValidationError):
            self.env['legal.deadline.rule'].create({'name':'Invalid','days':0,'start_point':'manual','legal_reference':'test'})

    def test_audit_is_immutable(self):
        log=self.env['legal.audit.log'].create({'model_name':'res.partner','res_id':1,'operation':'test'})
        with self.assertRaises(Exception):log.unlink()


@tagged('post_install', '-at_install')
class TestReminderWindows(TransactionCase):
    """The reminder settings exist to be obeyed; both crons used to ignore them."""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.env.user.group_ids = [(4, self.env.ref('era_law_firm.group_legal_manager').id)]
        self.lawyer = self.env['res.users'].create({
            'name': 'Reminder Lawyer', 'login': 'reminder_lawyer',
            'company_id': self.company.id})
        partner = self.env['res.partner'].create({'name': 'موكل'})
        self.case = self.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'litigation',
            'stage_id': self.env.ref('era_law_firm.stage_intake').id})

    def _hearing(self, days_ahead):
        start = fields.Datetime.add(fields.Datetime.now(), days=days_ahead)
        hearing = self.env['legal.hearing'].create({
            'name': 'جلسة', 'case_id': self.case.id, 'lawyer_id': self.lawyer.id,
            'start_datetime': start, 'stop_datetime': fields.Datetime.add(start, hours=1)})
        hearing.action_confirm()
        return hearing

    def test_a_hearing_outside_the_window_is_left_alone(self):
        self.company.legal_hearing_reminder_days = 3
        far = self._hearing(10)
        self.env['legal.hearing']._cron_reminders()
        self.assertFalse(far.reminder_scheduled)

    def test_widening_the_window_brings_the_hearing_in(self):
        """With the old hard-coded single day this hearing was never reminded."""
        self.company.legal_hearing_reminder_days = 5
        soon = self._hearing(3)
        self.env['legal.hearing']._cron_reminders()
        self.assertTrue(soon.reminder_scheduled)

    def test_a_deadline_uses_its_own_longer_window(self):
        self.company.legal_deadline_reminder_days = 7
        deadline = self.env['legal.deadline'].create({
            'name': 'اعتراض', 'case_id': self.case.id, 'user_id': self.lawyer.id,
            'deadline_date': fields.Date.add(fields.Date.today(), days=4),
            'source': 'تبليغ'})
        deadline.action_confirm()
        self.env['legal.deadline']._cron_reminders()
        self.assertTrue(deadline.reminder_scheduled)

    def test_a_reminder_is_not_raised_twice(self):
        self.company.legal_hearing_reminder_days = 5
        soon = self._hearing(2)
        self.env['legal.hearing']._cron_reminders()
        before = self.env['mail.activity'].search_count([
            ('res_model', '=', 'legal.hearing'), ('res_id', '=', soon.id)])
        self.env['legal.hearing']._cron_reminders()
        after = self.env['mail.activity'].search_count([
            ('res_model', '=', 'legal.hearing'), ('res_id', '=', soon.id)])
        self.assertEqual(before, after)

    def test_the_default_city_reaches_a_new_case(self):
        self.company.legal_default_city = 'الرياض'
        partner = self.env['res.partner'].create({'name': 'موكل آخر'})
        case = self.env['legal.case'].create({
            'client_id': partner.id, 'case_type': 'consultation',
            'stage_id': self.env.ref('era_law_firm.stage_intake').id})
        self.assertEqual(case.city, 'الرياض')

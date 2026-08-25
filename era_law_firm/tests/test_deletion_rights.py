"""Deleting anything attached to a legal file is reserved to the legal manager."""

import base64

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import LegalCommon


@tagged('post_install', '-at_install')
class TestDeletionRights(LegalCommon):

    def setUp(self):
        super().setUp()
        self.worker = self.env['res.users'].create({
            'name': 'Case Lawyer', 'login': 'legal_test_delete_lawyer',
            'company_id': self.company.id, 'company_ids': [(6, 0, [self.company.id])],
            'group_ids': [(6, 0, [self.env.ref('era_law_firm.group_legal_lawyer').id,
                                  self.env.ref('base.group_user').id])]})
        self.boss = self.env['res.users'].create({
            'name': 'Firm Manager', 'login': 'legal_test_delete_manager',
            'company_id': self.company.id, 'company_ids': [(6, 0, [self.company.id])],
            'group_ids': [(6, 0, [self.env.ref('era_law_firm.group_legal_manager').id,
                                  self.env.ref('base.group_user').id])]})
        self.case = self._make_case(lawyer_id=self.worker.id)

    def test_a_lawyer_cannot_delete_records_on_their_own_case(self):
        hearing = self.env['legal.hearing'].create({
            'name': 'Session', 'case_id': self.case.id, 'lawyer_id': self.worker.id,
            'start_datetime': fields.Datetime.now(),
            'stop_datetime': fields.Datetime.add(fields.Datetime.now(), hours=1)})
        with self.assertRaises(AccessError):
            hearing.with_user(self.worker).unlink()

    def test_a_manager_can_delete_a_draft_record(self):
        hearing = self.env['legal.hearing'].create({
            'name': 'Session', 'case_id': self.case.id, 'lawyer_id': self.worker.id,
            'start_datetime': fields.Datetime.now(),
            'stop_datetime': fields.Datetime.add(fields.Datetime.now(), hours=1)})
        hearing.with_user(self.boss).unlink()
        self.assertFalse(hearing.exists())

    def test_a_lawyer_cannot_delete_a_document(self):
        document = self.env['legal.document'].create({
            'name': 'Draft Memo', 'case_id': self.case.id, 'owner_id': self.worker.id,
            'file_data': base64.b64encode(b'memo'), 'file_name': 'memo.txt'})
        with self.assertRaises(AccessError):
            document.with_user(self.worker).unlink()

    def test_a_lawyer_cannot_delete_a_party_or_a_time_entry(self):
        party = self.env['legal.case.party'].create({
            'case_id': self.case.id, 'partner_id': self.opponent.id, 'role': 'opponent'})
        with self.assertRaises(AccessError):
            party.with_user(self.worker).unlink()

        engagement = self._active_engagement(self.case)
        entry = self.env['legal.time.entry'].create({
            'name': 'Research', 'case_id': self.case.id, 'engagement_id': engagement.id,
            'user_id': self.worker.id, 'hours': 2, 'rate': 500})
        with self.assertRaises(AccessError):
            entry.with_user(self.worker).unlink()

    def test_a_lawyer_cannot_delete_an_activity_on_a_legal_record(self):
        activity = self.env['mail.activity'].with_user(self.worker).create({
            'res_model_id': self.env['ir.model']._get('legal.case').id,
            'res_id': self.case.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'File the statement of claim',
            'user_id': self.worker.id})
        with self.assertRaises(AccessError):
            activity.with_user(self.worker).unlink()
        activity.with_user(self.boss).unlink()
        self.assertFalse(activity.exists())

    def test_marking_an_activity_done_still_works_for_a_lawyer(self):
        """Odoo archives a completed activity rather than deleting it, so the
        guard must not stand in the way of finishing your own work."""
        activity = self.env['mail.activity'].with_user(self.worker).create({
            'res_model_id': self.env['ir.model']._get('legal.case').id,
            'res_id': self.case.id,
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'summary': 'Call the client',
            'user_id': self.worker.id})
        activity.with_user(self.worker).action_feedback(feedback='Done')
        self.assertFalse(activity.active, 'a completed activity should be archived, not deleted')

    def test_a_lawyer_cannot_delete_a_note_on_a_legal_record(self):
        message = self.case.with_user(self.worker).message_post(
            body='Client called about the hearing date.')
        with self.assertRaises(AccessError):
            message.with_user(self.worker).unlink()

    def test_the_guard_leaves_non_legal_records_alone(self):
        partner = self.env['res.partner'].create({'name': 'Unrelated'})
        message = partner.message_post(body='Nothing to do with a case.')
        message.unlink()
        self.assertFalse(message.exists())

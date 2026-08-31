"""A conflict check that only matches identical records finds nothing.

Duplicate contact records are the normal state of a client list — the same
person entered by two people, or with أ instead of ا. Matching on partner_id
alone means the firm is told there is no conflict, which is a
professional-liability failure rather than a data-quality annoyance.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestConflictMatching(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.client = cls.env['res.partner'].create({'name': 'شركة الأفق'})
        cls.lawyer = cls.env.user
        cls.stage = cls.env.ref('era_law_firm.stage_intake')

    def _case(self, name, state, parties):
        case = self.env['legal.case'].create({
            'name': name, 'client_id': self.client.id, 'company_id': self.company.id,
            'lawyer_id': self.lawyer.id, 'case_type': 'litigation',
            'stage_id': self.stage.id,
        })
        for partner, role in parties:
            self.env['legal.case.party'].create({
                'case_id': case.id, 'partner_id': partner.id, 'role': role,
                'company_id': self.company.id,
            })
        if state != 'draft':
            case.write({'state': state})
        return case

    def _check(self, case):
        check = self.env['legal.conflict.check'].create({
            'case_id': case.id, 'company_id': self.company.id})
        check.action_run_check()
        return check

    def test_the_same_person_entered_twice_is_still_a_conflict(self):
        """Two records, one human — the orthography differs, the person does not."""
        first = self.env['res.partner'].create({'name': 'محمد عبدالله السالم'})
        duplicate = self.env['res.partner'].create({'name': 'محمد عبد الله السالم'})
        old = self._case('قديمة', 'confirmed', [(first, 'opponent')])
        new = self._case('جديدة', 'draft', [(duplicate, 'opponent')])
        check = self._check(new)
        self.assertEqual(check.state, 'blocked',
                         'a duplicate contact record must not defeat the check')
        self.assertEqual(check.line_ids.match_basis, 'normalised_name')
        self.assertEqual(check.line_ids.source_case_id, old)

    def test_a_shared_identity_number_beats_a_different_name(self):
        """People change how their name is written; an ID number is the person."""
        first = self.env['res.partner'].create({
            'name': 'محمد السالم', 'legal_identity_number': '1012345678'})
        same_person = self.env['res.partner'].create({
            'name': 'م. السالم', 'legal_identity_number': '1012345678'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(same_person, 'opponent')]))
        self.assertEqual(check.state, 'blocked')
        self.assertEqual(check.line_ids.match_basis, 'identity_number')

    def test_identity_numbers_match_despite_formatting(self):
        first = self.env['res.partner'].create({
            'name': 'مؤسسة أ', 'legal_registration_number': '4030-123456'})
        same = self.env['res.partner'].create({
            'name': 'مؤسسة ب', 'legal_registration_number': '4030123456'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(same, 'opponent')]))
        self.assertEqual(check.state, 'blocked')

    def test_a_short_number_is_not_an_identity(self):
        """'12' is not evidence of anything; it must not manufacture conflicts."""
        first = self.env['res.partner'].create({
            'name': 'أحمد', 'legal_identity_number': '12'})
        other = self.env['res.partner'].create({
            'name': 'سعيد', 'legal_identity_number': '12'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(other, 'opponent')]))
        self.assertEqual(check.state, 'clear')

    def test_unrelated_parties_still_clear(self):
        first = self.env['res.partner'].create({'name': 'خالد الأحمد'})
        other = self.env['res.partner'].create({'name': 'فهد المطيري'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(other, 'opponent')]))
        self.assertEqual(check.state, 'clear', 'no match must not block an engagement')

    def test_the_same_record_still_matches_and_says_so(self):
        shared = self.env['res.partner'].create({'name': 'سالم القحطاني'})
        self._case('قديمة', 'confirmed', [(shared, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(shared, 'opponent')]))
        self.assertEqual(check.state, 'blocked')
        self.assertEqual(check.line_ids.match_basis, 'same_partner')

    def test_a_draft_case_is_not_a_conflict_source(self):
        """An engagement nobody accepted yet cannot conflict with anything."""
        first = self.env['res.partner'].create({'name': 'ناصر العتيبي'})
        self._case('مسودة', 'draft', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(first, 'opponent')]))
        self.assertEqual(check.state, 'clear')

    def test_orthographic_variants_are_the_same_name(self):
        """أ/ا and ة/ه are typing, not identity."""
        first = self.env['res.partner'].create({'name': 'أحمد المطيرة'})
        variant = self.env['res.partner'].create({'name': 'احمد المطيره'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(variant, 'opponent')]))
        self.assertEqual(check.state, 'blocked')
        self.assertEqual(check.line_ids.match_basis, 'normalised_name')

    def test_different_people_are_not_merged_by_the_name_key(self):
        """Stripping spaces must not make two people into one."""
        first = self.env['res.partner'].create({'name': 'سعد الغامدي'})
        other = self.env['res.partner'].create({'name': 'سعود الغامدي'})
        self._case('قديمة', 'confirmed', [(first, 'opponent')])
        check = self._check(self._case('جديدة', 'draft', [(other, 'opponent')]))
        self.assertEqual(check.state, 'clear')

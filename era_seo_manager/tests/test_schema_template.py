"""Tests for era.seo.schema.template model validation.

Per SPEC §8.1.1 / CLAUDE.md §6.
"""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase


class TestSchemaTemplate(TransactionCase):
    """Model-level tests: body validation, @type cross-check, unique code."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env['era.seo.schema.template']

    def _make(self, **kwargs):
        defaults = {
            'name': 'Test Template',
            'code': 'test_tpl_unique',
            'schema_type': 'Thing',
            'category': 'core',
            'body': '{"@type": "Thing", "name": "Static"}',
        }
        defaults.update(kwargs)
        return self.Template.create(defaults)

    # --- body JSON validation -------------------------------------------------

    def test_valid_body_accepted(self):
        tpl = self._make()
        self.assertTrue(tpl.id)

    def test_body_with_placeholder_accepted(self):
        tpl = self._make(body='{"@type": "Thing", "name": {{ record.name }}}')
        self.assertTrue(tpl.id)

    def test_invalid_json_body_rejected(self):
        with self.assertRaises(ValidationError):
            self._make(code='bad_json_tpl', body='{"@type": "Thing", "name": }')

    def test_unclosed_brace_rejected(self):
        with self.assertRaises(ValidationError):
            self._make(code='bad_brace_tpl', body='{"@type": "Thing"')

    # --- @type cross-check ----------------------------------------------------

    def test_schema_type_mismatch_raises(self):
        with self.assertRaises(ValidationError):
            self._make(
                code='type_mismatch_tpl',
                schema_type='Organization',
                body='{"@type": "Person", "name": "Test"}',
            )

    def test_schema_type_placeholder_in_body_skipped(self):
        # If @type itself is a placeholder, body_type == '' after strip → no error.
        tpl = self._make(
            code='placeholder_type_tpl',
            schema_type='Thing',
            body='{"@type": {{ schema_type }}, "name": "X"}',
        )
        self.assertTrue(tpl.id)

    # --- required_fields_json validation -------------------------------------

    def test_valid_required_fields_array(self):
        tpl = self._make(
            code='req_fields_tpl',
            required_fields_json='["record.seo_title", "company.name"]',
        )
        self.assertEqual(tpl._get_required_fields(), ['record.seo_title', 'company.name'])

    def test_invalid_required_fields_raises(self):
        with self.assertRaises(ValidationError):
            self._make(
                code='bad_req_fields_tpl',
                required_fields_json='{"not": "an array"}',
            )

    def test_empty_required_fields_returns_empty_list(self):
        tpl = self._make(code='empty_req_tpl')
        self.assertEqual(tpl._get_required_fields(), [])

    # --- Unique code constraint -----------------------------------------------

    def test_duplicate_code_raises(self):
        self._make(code='unique_code_test')
        with self.assertRaises(Exception):
            self._make(code='unique_code_test', name='Duplicate')

    # --- active / is_default ---------------------------------------------------

    def test_active_default_true(self):
        tpl = self._make(code='active_default_tpl')
        self.assertTrue(tpl.active)

    def test_is_default_default_false(self):
        tpl = self._make(code='is_default_tpl')
        self.assertFalse(tpl.is_default)

# -*- coding: utf-8 -*-
"""Unit tests for the record-driven PII redactor (PDPL).

Covers the concrete Arabic case from the design: a prompt with a customer name
(appearing in full and as the family name alone) + a phone in LOCAL format while
the record stores it INTERNATIONAL + an email — masked on send, restored on
return, with fail-safe behaviour for unmapped PII and mangled placeholders.
"""
from odoo.tests import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_base.services.pii_redaction import (
    Redactor, RedactionError,
)


@tagged("post_install", "-at_install")
class TestPiiRedaction(TransactionCase):

    def _arabic_redactor(self):
        r = Redactor()
        r.add("PERSON", "محمد عبدالله القحطاني", split_parts=True)
        r.add("PHONE", "+966501234567")
        r.add("EMAIL", "m.qahtani@example.com")
        return r

    def test_mask_all_occurrences_and_formats(self):
        r = self._arabic_redactor()
        prompt = (
            "العميل محمد عبدالله القحطاني مهتم بالعقار. تواصل معه على الرقم "
            "0501234567 أو عبر البريد m.qahtani@example.com. اكتب رسالة متابعة "
            "للسيد القحطاني."
        )
        masked, leftover = r.mask(prompt)
        # Real PII must be gone.
        self.assertNotIn("محمد عبدالله القحطاني", masked)
        self.assertNotIn("القحطاني", masked)          # family name alone too
        self.assertNotIn("m.qahtani@example.com", masked)
        self.assertNotIn("0501234567", masked)         # local form matched
        # No unmapped PII left over -> not a block.
        self.assertEqual(leftover, [])

    def test_roundtrip_reinsertion(self):
        r = self._arabic_redactor()
        prompt = "راسل محمد عبدالله القحطاني على 0501234567"
        masked, _ = r.mask(prompt)
        # Model echoes the placeholders back verbatim (typical good case).
        restored = r.unmask(masked)
        self.assertIn("محمد عبدالله القحطاني", restored)
        self.assertIn("+966501234567", restored)
        self.assertNotIn("[[PII:", restored)

    def test_unmapped_pii_is_flagged_for_block(self):
        # A phone NOT tied to the record must be caught by the secondary net.
        r = Redactor()
        r.add("PERSON", "محمد القحطاني", split_parts=True)
        masked, leftover = r.mask("اتصل على 0559876543 الآن")
        self.assertTrue(leftover, "secondary net must flag unmapped phone")
        self.assertEqual(leftover[0][0], "PHONE")

    def test_mangled_placeholder_raises(self):
        r = self._arabic_redactor()
        # Model returned a broken token -> must refuse, never leak/guess.
        with self.assertRaises(RedactionError):
            r.unmask("مرحباً [[PII:PERSON:1 ، تم الإرسال")

    def test_unknown_placeholder_raises(self):
        r = self._arabic_redactor()
        with self.assertRaises(RedactionError):
            r.unmask("قيمة غير معروفة [[PII:PERSON:99]]")

# -*- coding: utf-8 -*-
"""Matching, which is where a wrong answer is decided long before a step is."""

from odoo.tests.common import TransactionCase

from ..models import text_match


class TestNormalisation(TransactionCase):

    def test_hamza_and_feminine_ending_fold(self):
        """Somebody typing without hamza reaches what somebody typing it wrote."""
        self.assertEqual(
            text_match.normalize("الأسعار"), text_match.normalize("الاسعار")
        )
        self.assertEqual(
            text_match.normalize("فاتورة"), text_match.normalize("فاتوره")
        )

    def test_harakat_are_ignored(self):
        self.assertEqual(
            text_match.normalize("مُنْتَج"), text_match.normalize("منتج")
        )


class TestBrokenPlurals(TransactionCase):
    """The singular a user types has to reach the plural a menu is named with.

    Every pair here was a question that went unanswered before these shapes
    were handled, with the menu one click away the whole time.
    """

    PAIRS = [
        ("عرض", "عروض"),
        ("سعر", "الأسعار"),
        ("عميل", "عملاء"),
        ("قائمة", "قوائم"),
        ("فاتورة", "فواتير"),
        ("بند", "بنود"),
        ("صنف", "أصناف"),
        ("حقل", "حقول"),
    ]

    def test_singular_meets_its_plural(self):
        for singular, plural in self.PAIRS:
            with self.subTest(singular=singular):
                one = text_match._variants(text_match.normalize(singular))
                many = text_match._variants(text_match.normalize(plural))
                self.assertTrue(
                    one & many,
                    "%s and %s never meet" % (singular, plural),
                )

    def test_sound_plurals_still_work(self):
        """The suffix stripping that was there before was not traded away."""
        one = text_match._variants(text_match.normalize("منتج"))
        many = text_match._variants(text_match.normalize("منتجات"))
        self.assertTrue(one & many)

    def test_unrelated_words_stay_apart(self):
        """A rule that folds everything together answers everything wrongly."""
        self.assertFalse(
            text_match._variants(text_match.normalize("عميل"))
            & text_match._variants(text_match.normalize("مصنع"))
        )


class TestAgreement(TransactionCase):

    def test_a_shared_word_alone_is_not_agreement(self):
        """This is the shape that sent a leave question into maintenance."""
        agreement, dummy = text_match.balanced("طلب اجازة", "طلبات الصيانة")
        self.assertLess(agreement, 0.8)

    def test_saying_the_same_thing_agrees_completely(self):
        agreement, dummy = text_match.balanced("جهات الاتصال", "جهات الاتصال")
        self.assertEqual(agreement, 1.0)

    def test_a_question_naming_only_the_menu_agrees(self):
        """"Where do I find Discuss" spends two words on neither."""
        agreement, dummy = text_match.balanced("وين ألقى المناقشة", "المناقشة")
        self.assertEqual(agreement, 1.0)


class TestAskingToBeShown(TransactionCase):
    """"Show me the invoices" names one thing, and it is not the showing."""

    def test_a_request_to_see_something_agrees_with_it(self):
        covered, dummy = text_match.balanced("اعرض الفواتير", "الفواتير")
        self.assertEqual(covered, 1.0)

    def test_the_english_of_it_too(self):
        covered, dummy = text_match.balanced("show me the invoices", "Invoices")
        self.assertEqual(covered, 1.0)

    def test_a_quotation_is_a_thing_not_a_request_to_see_one(self):
        """عرض on its own is the noun, and dropping it would lose the menu."""
        covered, dummy = text_match.balanced("عرض سعر", "عروض الأسعار")
        self.assertEqual(covered, 1.0)

    def test_a_verb_that_names_the_work_is_never_dropped(self):
        """اطبع and انشئ carry most of the meaning of a how-do-I question."""
        self.assertIn("اطبع", text_match.question_tokens("كيف اطبع الفاتورة"))


class TestOneWordIsNotAnAnswer(TransactionCase):
    """A generated tour is described by a whole question somebody once asked.

    Measured on the live database on 2026-08-01: "كيف اعمل فاتورة" scored
    exactly 0.500 against the walkthrough built for "كيف اعمل منتج تصنيعي
    بثلاث مكونات خام", on the strength of the shared verb عمل, and started a
    manufacturing walkthrough for somebody asking about an invoice.
    """

    MANUFACTURING = "كيف اعمل منتج تصنيعي بثلاث مكونات خام. كل مادة خام لها قيمة"

    def test_a_shared_verb_does_not_reach_another_task(self):
        result, dummy = text_match.score("كيف اعمل فاتورة", self.MANUFACTURING)
        self.assertLess(
            result, 0.5, "one verb in common must not clear the threshold")

    def test_the_question_it_was_built_for_still_reaches_it(self):
        result, dummy = text_match.score(self.MANUFACTURING, self.MANUFACTURING)
        self.assertGreaterEqual(result, 0.5)

    def test_the_same_task_worded_differently_still_reaches_it(self):
        result, dummy = text_match.score(
            "اضافة منتج جديد", "كيف اضيف منتج جديد")
        self.assertGreaterEqual(
            result, 0.5, "a rewording of the same question must still match")

    def test_an_administrator_keyword_still_wins(self):
        """"Also Matches" is a decision about the question, not the tour."""
        result, dummy = text_match.score(
            "تذكرة دعم", "مكتب المساعدة", keywords="تذكرة دعم")
        self.assertGreaterEqual(result, 0.5)


class TestHalfAQuestionIsNotAnAnswer(TransactionCase):
    """Agreement is a mean of two directions, and a short tour rides over it.

    Measured on the live database: "امر بيع مع اضافة عميل جديد" agreed 0.75
    with a walkthrough recorded for "اضافة عميل جديد" — the tour was covered
    completely, the question only three fifths — and answered in no time at
    all, dropping the sale order half without a word.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]

    def _tour(self, description):
        from ..models import tour_builder
        return self.tours.create({
            "name": "cov_%s" % abs(hash(description)),
            "custom": True,
            "assistant_enabled": True,
            "assistant_generated": True,
            # Stamped, or the version filter withholds it and the test would
            # be measuring staleness rather than coverage.
            "assistant_builder_version": tour_builder.BUILDER_VERSION,
            # A generated walkthrough with no menus left is withheld, so give
            # it one: this measures coverage, not the menu gate.
            "assistant_menu_ids": [
                (6, 0, self.env["ir.ui.menu"].search([], limit=1).ids)],
            "assistant_description": description,
        })

    def test_a_tour_for_half_the_question_does_not_answer_it(self):
        self._tour("اضافة عميل جديد")
        tour, score, dummy = self.tours._assistant_best_match(
            "امر بيع مع اضافة عميل جديد")
        self.assertFalse(
            tour, "half an answer given instantly is worse than working it out")

    def test_the_question_it_was_built_for_still_reaches_it(self):
        made = self._tour("اضافة عميل جديد")
        tour, score, dummy = self.tours._assistant_best_match("اضافة عميل جديد")
        self.assertEqual(tour, made)

    def test_a_rewording_still_reaches_it(self):
        made = self._tour("كيف اضيف عميل جديد")
        tour, dummy, ignored = self.tours._assistant_best_match("اضف عميل جديد")
        self.assertEqual(tour, made)

    def test_the_floor_can_be_tuned_per_database(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web_tour_assistant.match_coverage", "0.0")
        self._tour("اضافة عميل جديد")
        tour, dummy, ignored = self.tours._assistant_best_match(
            "امر بيع مع اضافة عميل جديد")
        self.assertTrue(tour, "a database that wants the old behaviour may have it")

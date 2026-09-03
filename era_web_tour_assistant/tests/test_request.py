# -*- coding: utf-8 -*-
"""What a user gets back, which is the only part of this they ever see."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestAsking(TransactionCase):

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"]

    def test_an_empty_question_is_refused(self):
        with self.assertRaises(UserError):
            self.requests.ask("   ")

    def test_a_question_of_only_question_words_names_no_task(self):
        """"How do I?" names nothing, so nothing is recorded as demand.

        It used to raise, which put a red dialog in front of somebody whose
        only mistake was not having finished their sentence. The question is
        still not counted — the queue is a ranked list of what staff could not
        work out, and "كيف" is not an entry in it — but they are told so where
        they are typing.
        """
        answer = self.requests.ask("كيف ممكن؟")
        self.assertEqual(answer["state"], "queued")
        self.assertTrue(answer["message"])
        self.assertFalse(
            self.requests.sudo().search([("name", "=", "كيف ممكن؟")]),
            "a question naming no task must not become demand",
        )

    def test_the_same_question_asked_twice_is_one_record(self):
        """Two people phrasing it differently are one demand, not two."""
        with patch.object(
            type(self.env["tour.assistant.builder"]), "build_with_reason",
            return_value=(self.env["web_tour.tour"], ""),
        ):
            first = self.requests.ask("اضافة الطلب")
            second = self.requests.ask("اضافه طلب")
        # Matched on the id the two calls came back with, not on a search for
        # the word: other records carry it too, and a loose search measures the
        # demo data rather than the folding.
        self.assertEqual(first["request_id"], second["request_id"])
        record = self.requests.sudo().browse(first["request_id"])
        self.assertEqual(record.ask_count, 2)

    def test_a_failure_becomes_a_queue_entry_not_a_traceback(self):
        """Somebody typed a question and pressed a button."""
        with patch.object(
            type(self.env["tour.assistant.builder"]), "build_with_reason",
            side_effect=RuntimeError("boom"),
        ):
            answer = self.requests.ask("سؤال يفشل بناؤه")
        self.assertEqual(answer["state"], "queued")

    def test_the_reason_the_planner_gave_reaches_the_user(self):
        """A client with no HR deserves to be told so, not left waiting."""
        excuse = "تطبيق الرواتب غير مثبت في هذه القاعدة."
        with patch.object(
            type(self.env["tour.assistant.builder"]), "build_with_reason",
            return_value=(self.env["web_tour.tour"], excuse),
        ):
            answer = self.requests.ask("كشف الرواتب")
        self.assertEqual(answer["message"], excuse)
        record = self.requests.sudo().search([("name", "=", "كشف الرواتب")])
        self.assertEqual(record.build_error, excuse)


class TestEstimate(TransactionCase):

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"].sudo()

    def _record(self, seconds, index):
        return self.requests.create({
            "name": "q%d" % index, "question_key": "k%d" % index,
            "build_seconds": seconds,
        })

    def test_nothing_is_quoted_before_there_is_anything_to_quote(self):
        """A first user told "about a second" who waits nine stops believing."""
        self.requests.search([]).unlink()
        for index in range(self.requests.MIN_TIMINGS - 1):
            self._record(3.0, index)
        self.assertEqual(self.requests.build_estimate(), (0.0, 0.0))

    def test_the_quote_is_the_middle_and_the_slow_end(self):
        """Not the fastest: those are two populations, not one."""
        self.requests.search([]).unlink()
        for index, seconds in enumerate([0.1] * 5 + [5.0] * 5 + [9.0] * 2):
            self._record(seconds, index)
        middle, upper = self.requests.build_estimate()
        self.assertGreater(middle, 0.1)
        self.assertGreaterEqual(upper, middle)


class _Answering:
    """An account that says one fixed thing."""

    name = "test"

    def __init__(self, answer):
        self.answer = answer

    def generate_text(self, *args, **kwargs):
        return self.answer


class TestPlannerAnswer(TransactionCase):
    """Nothing the model says is trusted beyond being read."""

    def setUp(self):
        super().setUp()
        self.planner = self.env["tour.assistant.planner"]

    def test_a_number_that_was_not_offered_is_discarded(self):
        self.assertEqual(self.planner._read_choice("61", 60), 0)
        self.assertEqual(self.planner._read_choice("0", 60), 0)

    def test_a_number_is_read_through_the_prose_around_it(self):
        self.assertEqual(self.planner._read_choice("الجواب: 3", 60), 3)
        self.assertEqual(self.planner._read_choice("  12  ", 60), 12)

    def test_an_answer_with_no_number_chooses_nothing(self):
        for answer in ("", None, "لا شيء", "abc"):
            with self.subTest(answer=answer):
                self.assertEqual(self.planner._read_choice(answer, 60), 0)

    def test_the_reason_is_taken_from_after_the_number(self):
        reason = self.planner._read_reason("0\nتطبيق المصروفات غير مثبت.")
        self.assertEqual(reason, "تطبيق المصروفات غير مثبت.")

    def test_a_bare_refusal_offers_no_reason(self):
        self.assertEqual(self.planner._read_reason("0"), "")

    def _offered(self, count=5):
        return self.env["ir.ui.menu"].search([], limit=count)

    def test_a_plan_of_several_stages_is_read_in_order(self):
        offered = self._offered()
        stages = self.planner._read_plan(
            "2 | create | أنشئ المواد الخام وحدد قيمتها | standard_price\n"
            "1 | visit  | راجع النتيجة |",
            offered,
        )
        self.assertEqual([stage["menu"] for stage in stages],
                         [offered[1], offered[0]])
        self.assertEqual(stages[0]["fields"], ["standard_price"])
        self.assertTrue(stages[0]["create"])
        self.assertFalse(stages[1]["create"])

    def test_a_stage_naming_a_menu_that_was_not_offered_is_dropped(self):
        """The list it was handed is the whole of what it may point at."""
        offered = self._offered(3)
        stages = self.planner._read_plan(
            "99 | create | شاشة لا وجود لها |\n1 | visit | شاشة حقيقية |",
            offered,
        )
        self.assertEqual([stage["menu"] for stage in stages], [offered[0]])

    def test_the_same_screen_twice_is_one_stage(self):
        offered = self._offered(3)
        stages = self.planner._read_plan(
            "1 | create | أنشئ |\n1 | visit | راجع |", offered
        )
        self.assertEqual(len(stages), 1)

    def test_a_plan_longer_than_the_cap_is_cut(self):
        offered = self._offered(10)
        answer = "\n".join(
            "%d | visit | مرحلة |" % index for index in range(1, 9)
        )
        self.assertEqual(
            len(self.planner._read_plan(answer, offered)),
            self.planner.MAX_STAGES,
        )

    def test_prose_around_the_plan_does_not_become_a_stage(self):
        offered = self._offered(5)
        stages = self.planner._read_plan(
            "إليك الخطة:\n1 | create | أنشئ المنتج |\nبالتوفيق.", offered
        )
        self.assertEqual(len(stages), 1)

    def test_a_refusal_is_no_plan_at_all(self):
        offered = self._offered(5)
        self.assertEqual(
            self.planner._read_plan("0\nتطبيق التصنيع غير مثبت.", offered), []
        )

    def test_the_listing_names_the_record_each_menu_opens(self):
        """Purchase / Orders / Vendors is res.partner, and only this says so."""
        menu = self.env["ir.ui.menu"].search([("action", "!=", False)], limit=1)
        listing = self.planner._listing(menu)
        model_name = menu.sudo().action.res_model
        self.assertIn("[%s]" % model_name, listing)

    def test_a_menu_with_no_action_is_listed_without_brackets(self):
        menu = self.env["ir.ui.menu"].create({"name": "Assistant listing test"})
        self.assertEqual(self.planner._listing(menu),
                         "1. Assistant listing test")

    def test_every_menu_of_this_database_fits_the_cap(self):
        """A cap reached silently reads as coverage it does not have."""
        from odoo.addons.era_web_tour_assistant.models import tour_planner
        reachable = self.env["ir.ui.menu"].search([("action", "!=", False)])
        reachable = reachable._filter_visible_menus()
        self.assertLessEqual(len(reachable), tour_planner.MAX_CANDIDATES)

    def test_a_bare_number_is_still_a_one_screen_plan(self):
        """Format drift is the failure to expect from a model told a shape."""
        offered = self._offered(5)
        with patch.object(type(self.planner), "_enabled", return_value=True), \
                patch.object(type(self.planner), "_account",
                             return_value=_Answering("2")):
            stages, reason = self.planner.plan_task("اضافة عميل", offered)
        self.assertEqual([stage["menu"] for stage in stages], [offered[1]])
        self.assertEqual(reason, "")

    def test_a_dead_account_is_noticed(self):
        """A revoked login degrades the module silently unless something looks."""
        class Dead:
            name = "test"

            def generate_text(self, *args, **kwargs):
                raise RuntimeError("401 Unauthorized")

        with patch.object(type(self.planner), "_account", return_value=Dead()):
            self.assertFalse(self.planner.check_account())

    def test_an_account_answering_nothing_is_noticed(self):
        class Silent:
            name = "test"

            def generate_text(self, *args, **kwargs):
                return "   "

        with patch.object(type(self.planner), "_account", return_value=Silent()):
            self.assertFalse(self.planner.check_account())

    def test_a_working_account_passes(self):
        class Alive:
            name = "test"

            def generate_text(self, *args, **kwargs):
                return "1"

        with patch.object(type(self.planner), "_account", return_value=Alive()):
            self.assertTrue(self.planner.check_account())

    def test_nothing_is_asked_when_no_account_is_configured(self):
        with patch.object(type(self.planner), "_account", return_value=None):
            menu, reason = self.planner.choose(
                "اضافة عميل", self.env["ir.ui.menu"].search([], limit=5)
            )
        self.assertFalse(menu)
        self.assertEqual(reason, "")


class TestVeryShortQuestions(TransactionCase):
    """Somebody who has not said what they want yet is not making an error.

    Found by asking three hundred questions: "كيف" and "شي" raised a dialog
    while "مساعدة" and "افتح" came back with a sentence in the box the user was
    already looking at. The same act, answered two ways, and the dialog reads
    as a fault of theirs.
    """

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"]

    def test_a_question_word_alone_is_queued_not_raised(self):
        answer = self.requests.ask("كيف")
        self.assertEqual(answer["state"], "queued")
        self.assertTrue(answer["message"])

    def test_the_same_for_a_bare_noun(self):
        self.assertEqual(self.requests.ask("شي")["state"], "queued")

    def test_an_empty_box_is_still_refused(self):
        """Nothing typed at all is a different case, and stays an error."""
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.requests.ask("   ")


class TestWhatTheWorkerSubmits(TransactionCase):
    """A walkthrough the agent drove a browser to discover, then lost.

    The two gates that govern generated walkthroughs both closed on it: it was
    created without a builder version, which reads as older than every builder,
    and without menus, which reads as menus deleted under it. Minutes of a real
    browser, withheld from everybody and swept inside a day.
    """

    def setUp(self):
        super().setUp()
        # Through the worker gate rather than round it: the submission path is
        # what is being measured, and it starts with that check.
        self.env.user.group_ids = [
            (4, self.env.ref("era_web_tour_assistant.group_tour_assistant_worker").id)]
        self.requests = self.env["tour.assistant.request"].sudo()
        self.tours = self.env["web_tour.tour"].sudo()

    def _steps(self):
        return [
            {"trigger": '[data-menu-xmlid="base.menu_administration"]',
             "content": "افتح الإعدادات", "run": "click"},
            {"trigger": ".o_list_button_add", "content": "اضغط جديد",
             "run": "click"},
        ]

    def _submit(self):
        request = self.requests.create({
            "name": "سؤال يبنيه العامل", "question_key": "عامل يبني",
            "state": "queued",
        })
        result = self.requests.worker_submit(request.id, self._steps())
        return self.tours.search([("name", "=", result["tour"])], limit=1)

    def test_it_is_stamped_with_the_builder_version(self):
        from ..models import tour_builder
        self.assertEqual(
            self._submit().assistant_builder_version,
            tour_builder.BUILDER_VERSION)

    def test_its_menus_are_recovered_from_its_own_steps(self):
        tour = self._submit()
        self.assertIn(
            self.env.ref("base.menu_administration"), tour.assistant_menu_ids)

    def test_and_it_survives_both_gates(self):
        tour = self._submit()
        self.assertTrue(tour._assistant_is_visible_to_user())
        self.assertIn(tour, self.tours._assistant_candidates())

    def test_steps_naming_no_menu_leave_it_withheld(self):
        """The safe direction: nobody checked the reader can reach them."""
        request = self.requests.create({
            "name": "خطوات بلا قوائم", "question_key": "بلا قوائم",
            "state": "queued",
        })
        result = self.requests.worker_submit(
            request.id,
            [{"trigger": ".o_list_button_add", "content": "جديد", "run": "click"}])
        tour = self.tours.search([("name", "=", result["tour"])], limit=1)
        self.assertFalse(tour.assistant_menu_ids)
        self.assertFalse(tour._assistant_is_visible_to_user())


class TestAClaimNobodyFinished(TransactionCase):
    """A worker that died between claiming and answering took the question.

    Claiming marks it "building" so two workers cannot take the same one.
    Nothing marked it back, and claiming only ever looks at "queued" — so a
    crash, a lost connection or a container rebuilt under the worker left the
    question in a state no one would ever look at again. On a database where
    the worker is unreliable, the queue quietly empties itself.
    """

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"].sudo()

    def _claimed(self, minutes_ago):
        from datetime import timedelta
        from odoo import fields
        record = self.requests.create({
            "name": "سؤال مُطالَب به %d" % minutes_ago,
            "question_key": "مطالب %d" % minutes_ago,
            "state": "building",
        })
        # write_date is what the release measures, and creating sets it to now.
        self.env.cr.execute(
            "UPDATE tour_assistant_request SET write_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(minutes=minutes_ago), record.id))
        record.invalidate_recordset(["write_date"])
        return record

    def test_one_left_too_long_goes_back_in_the_queue(self):
        record = self._claimed(120)
        self.assertGreaterEqual(self.requests._release_abandoned_claims(), 1)
        record.invalidate_recordset(["state"])
        self.assertEqual(record.state, "queued")

    def test_one_still_being_worked_on_is_left_alone(self):
        """Reclaiming a live one would put two agents on the same question."""
        record = self._claimed(2)
        self.requests._release_abandoned_claims()
        record.invalidate_recordset(["state"])
        self.assertEqual(record.state, "building")

    def test_a_released_question_can_be_claimed_again(self):
        self.env.user.group_ids = [
            (4, self.env.ref("era_web_tour_assistant.group_tour_assistant_worker").id)]
        record = self._claimed(120)
        self.requests._release_abandoned_claims()
        claimed = self.requests.worker_claim(limit=10)
        self.assertIn(record.id, [row["id"] for row in claimed])


class TestTwoPeopleAskingAtOnce(TransactionCase):
    """The window is as wide as a build, not as wide as a database write.

    ask() looks the question up, works the answer out — six to sixteen seconds
    — and only then writes. Two people asking the same new question inside that
    window both find nothing and both create it, and the second one used to see
    a constraint violation instead of their walkthrough. The question two
    people ask at the same moment is the popular one this exists for.
    """

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"]

    def test_the_second_asker_gets_an_answer_not_a_violation(self):
        question = "كيف اسجل حضور الموظفين"
        key = " ".join(sorted({
            __import__("odoo").addons.era_web_tour_assistant.models.text_match.stem(word)
            for word in __import__("odoo").addons.era_web_tour_assistant.models
            .text_match.question_tokens(question)
        }))
        # The other person's record, already committed by the time this one
        # tries to write its own.
        other = self.requests.sudo().create({
            "name": question, "question_key": key, "state": "queued",
            "ask_count": 1,
        })

        empty = self.requests.sudo().browse()
        with patch.object(
            type(self.requests.sudo()), "search",
            side_effect=[empty, other],
        ), patch.object(
            type(self.env["tour.assistant.builder"]), "build_with_reason",
            return_value=(self.env["web_tour.tour"], ""),
        ):
            answer = self.requests.ask(question)

        self.assertEqual(answer["state"], "queued")
        self.assertEqual(
            self.requests.sudo().search_count([("question_key", "=", key)]), 1,
            "one question, one record, however many people asked it")

    def test_the_count_is_the_other_person_plus_this_one(self):
        """Losing the race must not lose their ask either."""
        question = "كيف اطبع كشف الرواتب"
        record = self.requests.sudo().create({
            "name": question, "question_key": "كشف راتب", "ask_count": 4,
            "state": "queued",
        })
        record.write({"ask_count": record.ask_count + 1})
        self.assertEqual(record.ask_count, 5)


class TestTheNumberThisLeansOn(TransactionCase):
    """Completion rate is the measurement, and nothing ever checked it moves.

    A question asked twenty times whose walkthrough is finished twice is the
    fault nobody complains about, and this module tells an owner to watch that
    column. It is called by the browser at the end of a tour, so the honest
    test is to call it the way the browser does and watch the counter.
    """

    def setUp(self):
        super().setUp()
        self.requests = self.env["tour.assistant.request"].sudo()
        self.tours = self.env["web_tour.tour"].sudo()

    def _walkthrough(self, stages=1):
        made = []
        following = self.tours.browse()
        for index in reversed(range(stages)):
            following = self.tours.create({
                "name": "completion_%d_%d" % (stages, index),
                "custom": True,
                "assistant_generated": True,
                "assistant_enabled": index == 0,
                "assistant_next_stage_id": following.id or False,
                # A stage naming no menus is withheld, and consume() then
                # treats every stage as the last one and counts a completion
                # at each — which is right for a chain whose remainder the
                # reader cannot reach, and not what this test is about.
                "assistant_menu_ids": [
                    (6, 0, self.env["ir.ui.menu"].search([], limit=1).ids)],
            })
            made.append(following)
        head = made[-1]
        for stage in made[:-1]:
            stage.assistant_first_stage_id = head.id
        return head

    def _request(self, head, asked=1):
        # Linked to the user who will finish it. Only a request its own asker
        # completes is counted, so that running a walkthrough reached some
        # other way cannot inflate anybody's numbers — a fixture that omits
        # the link measures that rule rather than the counter.
        return self.requests.create({
            "name": "سؤال إكمال %s" % head.name,
            "question_key": head.name,
            "state": "matched", "tour_id": head.id, "ask_count": asked,
            "user_ids": [(4, self.env.user.id)],
        })

    def _walk(self, head):
        stage = head
        while stage:
            name, stage = stage.name, stage.assistant_next_stage_id
            self.env["web_tour.tour"].consume(name)

    def test_finishing_one_records_one(self):
        head = self._walkthrough()
        record = self._request(head)
        self._walk(head)
        record.invalidate_recordset()
        self.assertEqual(record.completed_count, 1)
        self.assertAlmostEqual(record.completion_rate, 1.0, places=2)

    def test_a_chain_counts_once_not_once_per_stage(self):
        """Four stages counting themselves would make a long answer look
        four times as useful as a short one."""
        head = self._walkthrough(stages=4)
        record = self._request(head)
        self._walk(head)
        record.invalidate_recordset()
        self.assertEqual(record.completed_count, 1)

    def test_asking_without_finishing_moves_the_rate_down(self):
        head = self._walkthrough()
        record = self._request(head, asked=1)
        self._walk(head)
        record.invalidate_recordset()
        self.assertAlmostEqual(record.completion_rate, 1.0, places=2)
        record.write({"ask_count": 2})
        record.invalidate_recordset()
        self.assertAlmostEqual(record.completion_rate, 0.5, places=2)

    def test_a_walkthrough_this_user_did_not_ask_for_records_nothing(self):
        """Otherwise anybody could inflate a number by running somebody else's."""
        head = self._walkthrough()
        record = self.requests.create({
            "name": "سؤال شخص آخر", "question_key": "شخص آخر",
            "state": "matched", "tour_id": head.id, "ask_count": 5,
        })
        self._walk(head)
        record.invalidate_recordset()
        self.assertEqual(record.completed_count, 0)

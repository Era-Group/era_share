# -*- coding: utf-8 -*-
"""The builder, whose every output is a promise that a step points somewhere."""

from unittest.mock import patch

from lxml import etree

from odoo.tests.common import TransactionCase


class TestClickablePath(TransactionCase):
    """Only the levels the navbar draws as something to click become steps."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _path(self, length):
        menu = self.env["ir.ui.menu"]
        return [(menu, "x.m%d" % index) for index in range(length)]

    def test_a_short_path_is_kept_whole(self):
        for length in (1, 2):
            with self.subTest(length=length):
                path = self._path(length)
                self.assertEqual(self.builder._clickable_path(path), path)

    def test_headings_between_the_app_and_the_target_are_dropped(self):
        """A section with children is a label, and a label cannot be clicked."""
        path = self._path(4)
        kept = self.builder._clickable_path(path)
        self.assertEqual(kept, [path[0], path[1], path[-1]])

    def test_the_target_is_never_lost(self):
        for length in range(1, 7):
            with self.subTest(length=length):
                path = self._path(length)
                self.assertIn(path[-1], self.builder._clickable_path(path))


class TestCreationSteps(TransactionCase):
    """New and Save are only offered where they exist."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _menu_opening(self, view_mode, model="res.partner"):
        action = self.env["ir.actions.act_window"].create({
            "name": "Test", "res_model": model, "view_mode": view_mode,
        })
        return self.env["ir.ui.menu"].create({
            "name": "Test menu", "action": "ir.actions.act_window,%d" % action.id,
        })

    def test_a_form_only_action_gets_no_new_button(self):
        """Settings screens and wizards create nothing."""
        self.assertEqual(
            self.builder._creation_steps(self._menu_opening("form")), []
        )

    def _kanban_arch(self, arch):
        """Answer the kanban read with ``arch`` and leave the form read alone."""
        original = type(self.env["res.partner"]).get_view

        def get_view(records, *args, **kwargs):
            if kwargs.get("view_type") == "kanban":
                return {"arch": arch}
            return original(records, *args, **kwargs)

        return patch.object(type(self.env["res.partner"]), "get_view", get_view)

    def test_a_quick_create_kanban_gets_no_new_button(self):
        """Its New drops an inline card, and the fields are then nowhere."""
        menu = self._menu_opening("kanban,list,form")
        with self._kanban_arch('<kanban on_create="quick_create"/>'):
            self.assertEqual(self.builder._creation_steps(menu), [])

    def test_a_kanban_naming_an_action_gets_no_new_button(self):
        """A wizard opens instead, and its fields are its own."""
        menu = self._menu_opening("kanban,list,form")
        with self._kanban_arch('<kanban on_create="base.action_res_users"/>'):
            self.assertEqual(self.builder._creation_steps(menu), [])

    def test_a_plain_kanban_gets_its_own_new_button(self):
        """Products open a form from the kanban, and that is where tasks start."""
        menu = self._menu_opening("kanban,list,form")
        with self._kanban_arch("<kanban/>"):
            steps = self.builder._creation_steps(menu)
        self.assertTrue(steps)
        self.assertEqual(steps[0]["trigger"], ".o-kanban-button-new")

    def test_a_list_gets_new_and_save(self):
        steps = self.builder._creation_steps(self._menu_opening("list,form"))
        self.assertTrue(steps)
        self.assertEqual(steps[0]["trigger"], ".o_list_button_add")
        self.assertEqual(steps[-1]["trigger"], ".o_form_button_save")


class TestFieldsWorthNaming(TransactionCase):
    """A field named in a step has to be on the page when the form opens."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _fields_for(self, arch):
        with patch.object(
            type(self.env["res.partner"]), "get_view",
            return_value={"arch": arch},
        ):
            return dict(self.builder._required_visible_fields(
                self.env["res.partner"]
            ))

    def test_the_view_overrides_the_model_on_required(self):
        """crm.team declares alias_id required and its form says otherwise."""
        kept = self._fields_for(
            '<form><field name="name" required="0"/></form>'
        )
        self.assertNotIn("name", kept)

    def test_a_read_only_field_is_not_asked_for(self):
        kept = self._fields_for(
            '<form><field name="name" class="oe_read_only"/></form>'
        )
        self.assertNotIn("name", kept)

    def test_a_conditionally_hidden_field_is_not_asked_for(self):
        """Odoo removes it from the page; the condition cannot be evaluated."""
        kept = self._fields_for(
            '<form><field name="name" invisible="parent_id"/></form>'
        )
        self.assertNotIn("name", kept)

    def test_a_field_on_a_later_tab_is_not_asked_for(self):
        kept = self._fields_for(
            "<form><notebook>"
            '<page string="One"><field name="function"/></page>'
            '<page string="Two"><field name="name"/></page>'
            "</notebook></form>"
        )
        self.assertNotIn("name", kept)

    def test_a_field_inside_a_subview_is_not_on_this_form(self):
        """One2many columns belong to another record on another screen."""
        kept = self._fields_for(
            "<form>"
            '<field name="child_ids"><list><field name="name" required="1"/></list></field>'
            "</form>"
        )
        self.assertNotIn("name", kept)

    def test_a_field_the_form_opens_showing_is_kept(self):
        # Required in the view rather than the model, because the view has the
        # last word in both directions and res.partner does not demand a name.
        kept = self._fields_for('<form><field name="name" required="1"/></form>')
        self.assertIn("name", kept)


class TestNamedFields(TransactionCase):
    """What the planner asks to point out still has to be on the screen."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]
        self.partner = self.env["res.partner"]

    def _named(self, names, arch='<form><field name="function"/></form>'):
        with patch.object(
            type(self.partner), "get_view", return_value={"arch": arch},
        ):
            return [name for name, dummy in
                    self.builder._named_fields(self.partner, names)]

    def test_a_field_the_form_draws_is_pointed_at(self):
        self.assertEqual(self._named(["function"]), ["function"])

    def test_a_field_name_that_does_not_exist_is_dropped(self):
        """The planner is told not to guess. This is what happens when it does."""
        self.assertEqual(self._named(["totally_made_up_field"]), [])

    def test_a_real_field_the_form_does_not_draw_is_dropped(self):
        """It exists on the model, which is not the same as being on the page."""
        self.assertEqual(self._named(["credit_limit"]), [])

    def test_asking_for_nothing_points_at_nothing(self):
        self.assertEqual(self._named([]), [])


class TestPlanIntoSteps(TransactionCase):
    """A plan of several screens becomes a walkthrough of several screens."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _stage(self, xmlid, goal=""):
        menu = self.env.ref(xmlid)
        return {"menu": menu, "goal": goal, "create": False, "fields": []}

    def test_the_second_stage_does_not_say_open_again(self):
        """It starts wherever the last one finished, usually on a saved form."""
        stage = self._stage("base.menu_administration")
        first = self.builder._stage_steps(stage, opening=True)
        later = self.builder._stage_steps(stage, opening=False)
        self.assertTrue(first and later)
        self.assertNotEqual(first[0]["content"], later[0]["content"])

    def test_a_later_stage_leaves_the_app_it_is_in_first(self):
        """Inside an app the navbar carries that app's menus and no others."""
        stage = self._stage("base.menu_administration")
        later = self.builder._stage_steps(stage, opening=False)
        self.assertEqual(later[0]["trigger"], ".o_menu_toggle")
        first = self.builder._stage_steps(stage, opening=True)
        self.assertNotEqual(first[0]["trigger"], ".o_menu_toggle")

    def test_the_planner_sentence_lands_where_the_work_does(self):
        """On the click that arrives at the screen, not the one that leaves."""
        goal = "أنشئ المواد الخام الثلاث وحدد قيمة كل واحدة."
        steps = self.builder._stage_steps(
            self._stage("base.menu_administration", goal), opening=True
        )
        self.assertEqual(steps[-1]["content"], goal)

    def test_a_menu_that_cannot_be_pointed_at_is_skipped_not_fatal(self):
        """A menu made by hand has no external id and no other stable handle."""
        menu = self.env["ir.ui.menu"].create({"name": "Hand made"})
        stage = {"menu": menu, "goal": "", "create": False, "fields": []}
        self.assertEqual(self.builder._stage_steps(stage, opening=True), [])


class TestAmbiguity(TransactionCase):

    def test_a_name_shared_across_apps_is_refused(self):
        """Answering "add a customer" with a guess is the failure that matters."""
        builder = self.env["tour.assistant.builder"]
        with patch.object(
            type(self.env["tour.assistant.planner"]), "choose",
            return_value=(self.env["ir.ui.menu"], ""),
        ):
            menu, score = builder._best_menu("اضافة عميل")
        if menu:
            # Only meaningful where the demo data really does carry the clash;
            # if it does, the score must not be a confident one.
            self.assertLess(score, 1.0)


class TestStagesAreSeparateTours(TransactionCase):
    """A walkthrough of several screens is a chain, not one long queue.

    The interactive engine walks a tour's steps in order with no way to leave
    one out, so a stage somebody does not need — creating a raw material they
    already have — can only be skipped if it is a tour of its own.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _stages(self, *xmlids):
        return [
            {"menu": self.env.ref(xmlid), "goal": "", "create": False, "fields": []}
            for xmlid in xmlids
        ]

    def _build(self, *xmlids):
        stages = self._stages(*xmlids)
        with patch.object(
            type(self.builder), "_plan", return_value=(stages, ""),
        ):
            return self.builder.build_with_reason("سؤال من عدة مراحل")[0]

    def test_each_stage_becomes_its_own_tour(self):
        head = self._build("base.menu_administration", "base.menu_users")
        self.assertTrue(head, "a plan of two stages must produce a tour")
        self.assertTrue(head.assistant_next_stage_id, "the head must lead somewhere")

    def test_only_the_head_is_ever_offered(self):
        """A middle stage answers a question with the tail of its own answer."""
        head = self._build("base.menu_administration", "base.menu_users")
        self.assertTrue(head.assistant_enabled)
        self.assertFalse(head.assistant_next_stage_id.assistant_enabled)

    def test_a_completion_is_recorded_against_the_question(self):
        head = self._build("base.menu_administration", "base.menu_users")
        self.assertEqual(
            head.assistant_next_stage_id.assistant_first_stage_id, head,
            "a later stage must point back, or the completion lands nowhere",
        )
        self.assertFalse(head.assistant_first_stage_id, "the head is the head")

    def test_only_the_last_stage_celebrates(self):
        """A rainbow between two stages reads as done to somebody who is not."""
        head = self._build("base.menu_administration", "base.menu_users")
        self.assertFalse(head.rainbow_man_message)
        self.assertTrue(head.assistant_next_stage_id.rainbow_man_message)

    def test_a_freshly_built_walkthrough_carries_the_stamp(self):
        """Unstamped is older than everything: built once, then invisible.

        The line that stamps it did not apply for a while and nothing noticed,
        because a walkthrough answers the question that produced it directly
        and only later has to survive being matched.
        """
        head = self._build("base.menu_administration", "base.menu_users")
        from ..models import tour_builder
        node, stages = head, 0
        while node:
            stages += 1
            self.assertEqual(
                node.assistant_builder_version, tour_builder.BUILDER_VERSION,
                "every stage, not only the head")
            node = node.assistant_next_stage_id
        self.assertEqual(stages, 2)

    def test_it_is_still_matchable_after_being_built(self):
        head = self._build("base.menu_administration")
        head.assistant_description = "سؤال مختوم"
        found, dummy, ignored = self.env["web_tour.tour"]._assistant_best_match(
            "سؤال مختوم")
        self.assertEqual(found, head, "built and then invisible is the fault")

    def test_one_stage_is_still_one_tour(self):
        head = self._build("base.menu_administration")
        self.assertFalse(head.assistant_next_stage_id)
        self.assertTrue(head.rainbow_man_message)


class TestFieldTrigger(TransactionCase):
    """Where the pointer lands, which is not the same as what it resolves to."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]
        # The regex web_tour splits an anchor on, so a change to the trigger
        # that quietly stops splitting is caught here rather than in a browser.
        self.split = __import__("re").compile(r",\s*(?![^(]*\))")

    def test_the_control_is_tried_before_the_box_round_it(self):
        parts = self.split.split(self.builder._field_trigger("type"))
        self.assertEqual(len(parts), 2, "one fallback, and the :is() kept whole")
        self.assertIn(":is(", parts[0], "the control comes first or it never wins")
        self.assertEqual(parts[1].strip(), '.o_form_view [name="type"]')

    def test_the_fallback_still_resolves_a_widget_with_no_control(self):
        """A widget drawing no input of its own must not strand the step."""
        parts = self.split.split(self.builder._field_trigger("x"))
        self.assertNotIn(":is(", parts[1])


class TestSkippingAStage(TransactionCase):
    """A stage that creates something is one an existing record can answer."""

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]

    def _chain(self, skip_model="res.partner"):
        second = self.tours.create({"name": "stage_two", "custom": True})
        first = self.tours.create({
            "name": "stage_one",
            "custom": True,
            "assistant_generated": True,
            "assistant_next_stage_id": second.id,
            "assistant_skip_model": skip_model,
        })
        second.assistant_first_stage_id = first.id
        return first, second

    def test_the_browser_is_told_what_ends_the_stage_early(self):
        first, dummy = self._chain()
        info = self.tours.assistant_stage_info("stage_one")
        self.assertEqual(info.get("skip_model"), "res.partner")

    def test_a_last_stage_offers_nothing_to_skip_to(self):
        """Asking about the end of a walkthrough must not suggest a jump."""
        dummy, second = self._chain()
        self.assertEqual(self.tours.assistant_stage_info("stage_two"), {})

    def test_skipping_moves_to_the_next_stage(self):
        first, second = self._chain()
        self.env.user._assistant_start_tour(first)
        result = self.tours.assistant_skip_stage("stage_one")
        self.assertEqual(result["name"], "stage_two")
        self.assertEqual(
            self.env.user.assistant_pending_tour_id, second,
            "the onboarding preference must stay suspended across the jump",
        )

    def test_a_skipped_stage_is_not_a_completion(self):
        """It counts how many askers were carried through, and nobody was."""
        first, dummy = self._chain()
        self.env.user._assistant_start_tour(first)
        with patch.object(
            type(self.env["tour.assistant.request"]), "_register_completion",
        ) as recorded:
            self.tours.assistant_skip_stage("stage_one")
        recorded.assert_not_called()

    def test_a_stage_that_creates_nothing_is_never_skipped(self):
        first, dummy = self._chain(skip_model=False)
        self.assertEqual(
            self.tours.assistant_stage_info("stage_one").get("skip_model"), "")


class TestWhatIsActuallyOnTheForm(TransactionCase):
    """A field in the arch is not the same as a field on the screen."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _node(self, arch, name):
        from lxml import etree
        root = etree.fromstring(arch)
        return root.xpath('//field[@name="%s"]' % name)[0]

    def test_a_column_of_an_embedded_table_is_not_on_the_form(self):
        """Found on the live database walking a customer invoice.

        quantity and analytic_distribution live under the invoice lines and
        draw nothing until somebody adds a row, so both steps pointed at
        nothing while resolving perfectly in the arch.
        """
        arch = """
            <form>
              <field name="partner_id"/>
              <field name="invoice_line_ids">
                <list><field name="quantity"/></list>
              </field>
            </form>
        """
        self.assertFalse(
            self.builder._on_the_open_page(self._node(arch, "quantity")))

    def test_the_table_itself_is_still_offered(self):
        arch = """
            <form>
              <field name="invoice_line_ids">
                <list><field name="quantity"/></list>
              </field>
            </form>
        """
        self.assertTrue(
            self.builder._on_the_open_page(self._node(arch, "invoice_line_ids")))

    def test_a_plain_field_is_unaffected(self):
        arch = '<form><field name="partner_id"/></form>'
        self.assertTrue(
            self.builder._on_the_open_page(self._node(arch, "partner_id")))

    def test_a_conditionally_hidden_field_is_left_out(self):
        """Found walking a new expense: invisible="not can_be_reinvoiced"."""
        arch = """
            <form><field name="sale_order_id" invisible="not can_be_reinvoiced"/></form>
        """
        self.assertFalse(
            self.builder._on_the_open_page(self._node(arch, "sale_order_id")))

    def test_a_field_inside_a_hidden_wrapper_is_left_out(self):
        """And Quantity, which sits in a div marked invisible."""
        arch = """
            <form><div invisible="not product_has_cost">
              <field name="quantity"/>
            </div></form>
        """
        self.assertFalse(
            self.builder._on_the_open_page(self._node(arch, "quantity")))

    def test_a_modifier_that_is_literally_false_hides_nothing(self):
        arch = '<form><field name="partner_id" invisible="0"/></form>'
        self.assertTrue(
            self.builder._on_the_open_page(self._node(arch, "partner_id")))


class TestNamedFieldsGuard(TransactionCase):
    """The guard over what the planner names, in the case where it must hold.

    Found on the live database: a walkthrough for an expense pointed at
    Analytic Distribution, which that form does not draw. fields_get() reads an
    empty list as "no filter", so when none of the planner's names survived the
    filter it answered with every field on the model instead of none.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_no_surviving_name_yields_no_steps(self):
        model = self.env["res.users"]
        named = self.builder._named_fields(model, ["a_field_no_form_draws"])
        self.assertEqual(named, [])

    def test_a_name_the_form_does_draw_survives(self):
        model = self.env["res.partner"]
        shown, dummy = self.builder._form_fields(model)
        if not shown:
            self.skipTest("no form fields to test against")
        named = self.builder._named_fields(model, [shown[0]])
        self.assertEqual([name for name, label in named], [shown[0]])

    def test_an_invented_name_beside_a_real_one_is_dropped(self):
        model = self.env["res.partner"]
        shown, dummy = self.builder._form_fields(model)
        if not shown:
            self.skipTest("no form fields to test against")
        named = self.builder._named_fields(model, [shown[0], "invented_xyz"])
        self.assertEqual([name for name, label in named], [shown[0]])


class TestResumingAStage(TransactionCase):
    """web_tour hands the next stage back and the client drops it.

    startTour() returns without a word for any tour that is not in the
    javascript registry unless it is given fromDB, and web_tour's own chaining
    passes neither. Every tour this module generates lives only in the
    database, so a walkthrough ended silently at the end of its first stage.
    The browser asks for the pending stage instead of waiting to be told.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]

    def _chain(self):
        second = self.tours.create({"name": "resume_two", "custom": True})
        first = self.tours.create({
            "name": "resume_one", "custom": True, "assistant_generated": True,
            "assistant_next_stage_id": second.id,
        })
        second.assistant_first_stage_id = first.id
        return first, second

    def test_a_later_stage_is_offered_when_nothing_is_running(self):
        first, second = self._chain()
        self.env.user._assistant_start_tour(first)
        self.env.user._assistant_continue_tour(first, second)
        stage = self.tours.assistant_pending_stage()
        self.assertEqual(stage["name"], "resume_two")

    def test_the_first_stage_is_never_offered(self):
        """Whoever asked the question starts it; answering would race them."""
        first, dummy = self._chain()
        self.env.user._assistant_start_tour(first)
        self.assertFalse(self.tours.assistant_pending_stage())

    def test_nothing_pending_means_nothing_offered(self):
        self.env.user.sudo().assistant_pending_tour_id = False
        self.assertFalse(self.tours.assistant_pending_stage())


class TestLibraryAndStaleness(TransactionCase):
    """The list under the box, and dropping what an older builder wrote."""

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]
        from ..models import tour_builder
        self.version = tour_builder.BUILDER_VERSION

    def _tour(self, description, version=None, enabled=True):
        return self.tours.create({
            "name": "lib_%s" % abs(hash(description)),
            "custom": True,
            "assistant_enabled": enabled,
            "assistant_generated": True,
            "assistant_builder_version": self.version if version is None else version,
            # Same reason as the coverage fixtures: a generated walkthrough
            # that names no menus has had them deleted under it and is
            # withheld, which is not what these tests are measuring.
            "assistant_menu_ids": [
                (6, 0, self.env["ir.ui.menu"].search([], limit=1).ids)],
            "assistant_description": description,
        })

    def test_a_walkthrough_this_user_may_run_is_listed(self):
        made = self._tour("اضافة عميل جديد")
        listed = self.tours.assistant_library()
        self.assertIn(made.name, [row["name"] for row in listed])

    def test_one_an_older_builder_wrote_is_not_listed(self):
        self._tour("شيء قديم", version=self.version - 1)
        listed = self.tours.assistant_library()
        self.assertNotIn("شيء قديم", [row["label"] for row in listed])

    def test_one_an_older_builder_wrote_never_answers_a_question(self):
        """A fix that does not reach the walkthroughs already stored is not one."""
        self._tour("اضافة عميل جديد", version=self.version - 1)
        tour, dummy, ignored = self.tours._assistant_best_match("اضافة عميل جديد")
        self.assertFalse(tour)

    def test_the_stale_sweep_requeues_the_question(self):
        old = self._tour("سؤال قديم", version=self.version - 1)
        request = self.env["tour.assistant.request"].sudo().create({
            "name": "سؤال قديم", "question_key": "سؤال قديم",
            "tour_id": old.id, "state": "matched",
        })
        # At least one: a database that has been answering questions holds
        # older walkthroughs of its own, and the sweep takes those too.
        removed = self.tours._assistant_drop_stale()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(request.state, "queued")
        self.assertFalse(request.tour_id)

    def test_reporting_one_as_unhelpful_is_counted(self):
        made = self._tour("سؤال يُبلَّغ عنه")
        request = self.env["tour.assistant.request"].sudo().create({
            "name": "سؤال يُبلَّغ عنه", "question_key": "سؤال يُبلَّغ عنه",
            "tour_id": made.id, "state": "matched",
        })
        self.env["tour.assistant.request"].report_unhelpful(made.name)
        self.assertEqual(request.reported_count, 1)


class TestQuestionsThatGaveUp(TransactionCase):
    """A question the builder defeated three times was dead for good.

    Stopping after three attempts is right — something about it defeats the
    builder and handing it round forever wastes a worker. Never trying again is
    not: the builder was fixed eight times in two days, and none of those fixes
    reached a question that had already given up.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]
        self.setting = self.env["ir.config_parameter"].sudo()
        self.setting.set_param("era_web_tour_assistant.reopened_for_version", "0")

    def _stuck(self, key="هزم بناء"):
        # A distinct key each time: the model holds one record per question,
        # which is the point of the queue, so two fixtures cannot share one.
        return self.env["tour.assistant.request"].sudo().create({
            "name": "سؤال هزم البنّاء %s" % key, "question_key": key,
            "state": "queued", "build_attempts": 3,
            "build_error": "تعذّر البناء",
        })

    def test_an_upgrade_gives_it_another_chance(self):
        request = self._stuck()
        self.assertGreaterEqual(self.tours._assistant_reopen_after_upgrade(), 1)
        self.assertEqual(request.build_attempts, 0)
        self.assertFalse(request.build_error)

    def test_only_once_per_upgrade(self):
        """Nightly is not a reason to retry; a new builder is."""
        self._stuck()
        self.tours._assistant_reopen_after_upgrade()
        again = self._stuck("هزم بناء ثانيا")
        self.assertEqual(self.tours._assistant_reopen_after_upgrade(), 0)
        self.assertEqual(again.build_attempts, 3, "left alone until the next one")

    def test_a_question_already_answered_is_untouched(self):
        answered = self.env["tour.assistant.request"].sudo().create({
            "name": "سؤال مُجاب", "question_key": "مجاب",
            "state": "matched", "build_attempts": 2,
        })
        self.tours._assistant_reopen_after_upgrade()
        self.assertEqual(answered.build_attempts, 2)


class TestTheViewTheActionOpens(TransactionCase):
    """A model can have several form views, and an action chooses among them.

    Reading the model's default means reading a screen the user will not see.
    hr.leave draws employee_id twice in its default form — once invisible —
    while the action behind My Time Off opens a different view, so a step was
    written for a field that was not on the page it landed on and pointed at
    nothing. Found by walking sixty-five walkthroughs in a browser.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_view_named_outright_is_the_one_read(self):
        view = self.env["ir.ui.view"].search(
            [("model", "=", "res.partner"), ("type", "=", "form")], limit=1)
        action = self.env["ir.actions.act_window"].create({
            "name": "probe", "res_model": "res.partner", "view_id": view.id,
        })
        self.assertEqual(self.builder._form_view_id(action), view.id)

    def test_otherwise_the_form_entry_of_the_views_list_is(self):
        view = self.env["ir.ui.view"].search(
            [("model", "=", "res.partner"), ("type", "=", "form")], limit=1)
        action = self.env["ir.actions.act_window"].create({
            "name": "probe2", "res_model": "res.partner",
            "view_ids": [
                (0, 0, {"view_mode": "list", "sequence": 1}),
                (0, 0, {"view_mode": "form", "view_id": view.id, "sequence": 2}),
            ],
        })
        self.assertEqual(self.builder._form_view_id(action), view.id)

    def test_an_action_naming_none_falls_back_to_the_default(self):
        action = self.env["ir.actions.act_window"].create({
            "name": "probe3", "res_model": "res.partner",
        })
        self.assertIsNone(self.builder._form_view_id(action))
        shown, dummy = self.builder._form_fields(
            self.env["res.partner"], action)
        self.assertTrue(shown, "the default view is still read")

    def test_no_action_at_all_is_not_a_failure(self):
        self.assertIsNone(self.builder._form_view_id(None))

    def test_the_kanban_read_is_the_action_s_kanban_too(self):
        """The Accounting dashboard is a kanban that is not the default one."""
        view = self.env["ir.ui.view"].search(
            [("model", "=", "res.partner"), ("type", "=", "kanban")], limit=1)
        if not view:
            self.skipTest("no kanban view to test against")
        action = self.env["ir.actions.act_window"].create({
            "name": "probe4", "res_model": "res.partner", "view_id": view.id,
        })
        self.assertEqual(self.builder._view_id(action, "kanban"), view.id)
        self.assertIsNone(self.builder._view_id(action, "list"))


class TestOnlyWhatTheClientDraws(TransactionCase):
    """The planner may only be offered menus that are on the screen.

    Closing the visibility gate was not enough: the gate decides who may be
    offered a finished walkthrough, while the candidates decide what goes into
    one. The timesheet walkthrough was rebuilt after the gate was fixed and
    still aimed its last step at the menu the navbar does not draw, because the
    planner was still being handed it.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_menu_the_client_leaves_out_is_not_a_candidate(self):
        menu = self.env.ref(
            "hr_timesheet.timesheet_menu_activity_user",
            raise_if_not_found=False)
        if not menu:
            self.skipTest("hr_timesheet is not installed here")
        self.assertFalse(self.builder._clickable_menus(menu))

    def test_an_ordinary_menu_survives(self):
        menu = self.env.ref("base.menu_administration")
        self.assertEqual(self.builder._clickable_menus(menu), menu)

    def test_the_two_gates_agree(self):
        """What may be built with and what may be offered are one list."""
        menus = self.env["ir.ui.menu"].search([], limit=200)
        drawn = self.env["web_tour.tour"]._assistant_reachable_menu_ids()
        self.assertEqual(
            set(self.builder._clickable_menus(menus).ids),
            {menu.id for menu in menus if menu.id in drawn})


class TestNotInstalledVersusNotAllowed(TransactionCase):
    """The planner only sees this user's menus, so every refusal sounds alike.

    Measured with a plain employee, a stock keeper and an accountant: all three
    were told the Sales app was not installed, and it was installed the whole
    time. The employee then believes the system cannot do it, and the queue's
    column of "not installed" — which this module hands an owner as a
    purchasing conversation — fills with things they already own.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_screen_somebody_can_reach_is_named_as_access(self):
        """The question matches a menu for somebody, just not for them."""
        from unittest.mock import patch
        with patch.object(
            type(self.builder), "_best_agreement", return_value=0.95,
        ):
            told = self.builder._name_the_obstacle(
                "كيف اسوي امر بيع", "لا توجد قائمة المبيعات.")
        self.assertIn("لا توجد قائمة المبيعات.", told)
        self.assertNotEqual(told, "لا توجد قائمة المبيعات.")

    def test_a_screen_nobody_can_reach_is_left_as_it_was(self):
        from unittest.mock import patch
        with patch.object(
            type(self.builder), "_best_agreement", return_value=0.0,
        ):
            told = self.builder._name_the_obstacle(
                "كيف اسجل الحضور", "تطبيق الحضور غير مثبت.")
        self.assertEqual(told, "تطبيق الحضور غير مثبت.")

    def test_no_reason_stays_no_reason(self):
        self.assertEqual(self.builder._name_the_obstacle("سؤال", ""), "")

    def test_agreement_is_measured_without_the_ambiguity_veto(self):
        """_best_menu refuses to choose between apps; this only asks if any fit.

        Asked in the language the menus are named in, so the assertion is about
        the mechanism rather than about which translations this database has.
        """
        agreement = self.builder._best_agreement("Settings")
        self.assertGreater(agreement, 0.0)

    def test_a_question_about_nothing_here_agrees_with_nothing(self):
        self.assertEqual(self.builder._best_agreement("كيف اطبخ كبسة"), 0.0)


class TestReplayTellsTheServer(TransactionCase):
    """Running a walkthrough again from the list stopped at its first stage.

    Asking a question goes through ask(), which records the walkthrough against
    the user before handing it to the browser. Replaying from the list started
    the tour in the browser and told the server nothing — so when stage one
    finished there was no walkthrough in progress, nothing to move the mark to,
    and stage two never arrived. Reported from a real database, at step eight
    of twenty, with nothing anywhere saying why.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]
        from ..models import tour_builder
        menu = self.env["ir.ui.menu"].search([], limit=1)
        self.second = self.tours.sudo().create({
            "name": "replay_two", "custom": True, "assistant_generated": True,
            "assistant_builder_version": tour_builder.BUILDER_VERSION,
            "assistant_menu_ids": [(6, 0, menu.ids)],
        })
        self.head = self.tours.sudo().create({
            "name": "replay_one", "custom": True, "assistant_generated": True,
            "assistant_enabled": True,
            "assistant_builder_version": tour_builder.BUILDER_VERSION,
            "assistant_menu_ids": [(6, 0, menu.ids)],
            "assistant_next_stage_id": self.second.id,
        })
        self.second.assistant_first_stage_id = self.head.id
        self.env.user.sudo().assistant_pending_tour_id = False

    def test_beginning_records_the_walkthrough_against_the_user(self):
        self.assertTrue(self.tours.assistant_begin(self.head.name))
        self.assertEqual(self.env.user.assistant_pending_tour_id, self.head)

    def test_and_the_next_stage_then_arrives(self):
        """The whole point: without the record, the handover has nothing."""
        self.tours.assistant_begin(self.head.name)
        self.tours.consume(self.head.name)
        pending = self.tours.assistant_pending_stage()
        self.assertTrue(pending, "stage two has to be offered")
        self.assertEqual(pending["name"], self.second.name)

    def test_without_it_the_walkthrough_stops_where_stage_one_does(self):
        """The reported behaviour, kept as the thing that must not return."""
        self.tours.consume(self.head.name)   # never begun
        self.assertFalse(self.tours.assistant_pending_stage())

    def test_a_walkthrough_this_user_may_not_run_is_refused(self):
        self.head.assistant_menu_ids = [(6, 0, [])]
        self.assertFalse(self.tours.assistant_begin(self.head.name))

    def test_an_unknown_name_is_refused(self):
        self.assertFalse(self.tours.assistant_begin("no_such_tour"))


class TestAStageThatOnlyVisits(TransactionCase):
    """A stage that is not a creation used to be navigation and nothing else.

    Measured on the purchase walkthrough: stage one had seven steps — menus,
    New, two fields, Save — and stage two had four, all of them menu clicks,
    ending on a sentence that said "review the order and confirm it" with no
    pointer on anything. The reader is left looking at a list. It reads as the
    walkthrough giving up halfway, and that is what a user reported.
    """

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _stage(self, xmlid, create=False):
        return {"menu": self.env.ref(xmlid), "goal": "", "create": create,
                "fields": []}

    def test_a_visit_after_a_creation_opens_the_record(self):
        """There are users on any database, so the row is really there."""
        steps = self.builder._stage_steps(
            self._stage("base.menu_action_res_users"), opening=True,
            after_create=True)
        self.assertTrue(
            any(".o_data_row" in step["trigger"] for step in steps),
            "somebody cannot act on a record they have not opened")

    def test_a_visit_onto_an_empty_screen_points_at_no_row(self):
        """The guard that matters: counted now, as the asking user."""
        empty = self.env["ir.ui.menu"].create({
            "name": "Nothing here yet",
            "action": "ir.actions.act_window,%d" % self.env["ir.actions.act_window"].create({
                "name": "Nothing here yet", "res_model": "ir.filters"}).id,
        })
        self.env["ir.filters"].search([]).unlink()
        self.assertEqual(self.builder._handling_steps(empty), [],
                         "a step waiting on a row that is not coming")

    def test_a_visit_with_nothing_created_before_it_stays_navigation(self):
        """A step waiting for a row on an empty list is the old failure."""
        steps = self.builder._stage_steps(
            self._stage("base.menu_action_res_users"), opening=True,
            after_create=False)
        self.assertFalse(any(".o_data_row" in step["trigger"] for step in steps))

    def test_a_creation_stage_is_unchanged(self):
        steps = self.builder._stage_steps(
            self._stage("base.menu_action_res_users", create=True),
            opening=True, after_create=True)
        self.assertTrue(any(".o_list_button_add" in s["trigger"]
                            or ".o-kanban-button-new" in s["trigger"]
                            for s in steps))

    def test_a_model_with_no_header_buttons_adds_none(self):
        self.assertIsNone(
            self.builder._primary_button(self.env.ref("base.menu_action_res_users")))

    def test_a_condition_true_on_a_new_record_hides_its_button(self):
        """``state != 'draft'`` on a model that starts draft means shown."""
        users = self.env["res.users"]
        action = self.env.ref("base.action_res_users")
        self.assertTrue(self.builder._shown_on_a_saved_record(
            "not id", users, action),
            "a saved record has an id, so this condition does not hide it")
        self.assertTrue(self.builder._shown_on_a_saved_record("", users, action))

    def test_a_condition_on_something_that_is_not_a_field_settles_as_hidden(self):
        self.assertFalse(self.builder._shown_on_a_saved_record(
            "whatever_this_is", self.env["res.users"],
            self.env.ref("base.action_res_users")))

    def test_a_server_action_menu_still_names_its_model(self):
        """Inventory's Receipts opens a server action, not a window.

        Built here rather than borrowed from stock: a module upgrade loads
        only the models the module under test depends on — 170 of them, with
        no stock and no purchase — so a test resting on those would be
        measuring which modules happened to be loaded.
        """
        action = self.env["ir.actions.server"].create({
            "name": "Receipts, in miniature",
            "model_id": self.env.ref("base.model_res_users").id,
            "state": "code",
            "code": "action = model.some_action()",
        })
        menu = self.env["ir.ui.menu"].create({
            "name": "Receipts, in miniature",
            "action": "ir.actions.server,%d" % action.id,
        })
        self.assertEqual(self.builder._menu_model(menu), "res.users",
                         "a stage on such a menu stopped at navigation")


class TestTheLanguageAWalkthroughIsWrittenIn(TransactionCase):
    """Stored text, read later by a person — in their language, not the shell's."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_build_with_no_language_in_context_takes_the_users(self):
        self.env.user.lang = "en_US"
        spoken = []
        real = type(self.builder)._plan

        def watching(this, question):
            spoken.append(this.env.context.get("lang"))
            return real(this, question)

        with patch.object(type(self.builder), "_plan", watching):
            self.builder.with_context(lang=None).build_with_reason("anything")
        self.assertEqual(spoken, ["en_US"],
                         "the walkthrough would be stored in the source language")

    def test_a_language_already_in_context_is_left_alone(self):
        spoken = []
        real = type(self.builder)._plan

        def watching(this, question):
            spoken.append(this.env.context.get("lang"))
            return real(this, question)

        with patch.object(type(self.builder), "_plan", watching):
            self.builder.with_context(lang="en_US").build_with_reason("anything")
        self.assertEqual(spoken, ["en_US"])


class TestTheFilterTheScreenApplies(TransactionCase):
    """A menu shows a slice of a model as often as it shows the model."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _menu(self, domain):
        action = self.env["ir.actions.act_window"].create({
            "name": "A slice of the users",
            "res_model": "res.users",
            "domain": domain,
        })
        return self.env["ir.ui.menu"].create({
            "name": "A slice of the users",
            "action": "ir.actions.act_window,%d" % action.id,
        })

    def test_a_screen_whose_filter_leaves_nothing_gets_no_row_step(self):
        menu = self._menu("[('login', '=', 'nobody at all')]")
        self.assertEqual(self.builder._handling_steps(menu), [],
                         "the list is empty however many users exist")

    def test_a_screen_whose_filter_leaves_records_gets_one(self):
        menu = self._menu("[('id', '!=', 0)]")
        self.assertTrue(self.builder._handling_steps(menu))

    def test_a_domain_resting_on_context_counts_wide_rather_than_guessing(self):
        menu = self._menu("[('id', '=', context_key_nobody_set)]")
        self.assertEqual(self.builder._screen_domain(menu), [])
        self.assertTrue(self.builder._handling_steps(menu))


class TestACreationStageWithNothingInIt(TransactionCase):
    """New, then Save, and no field between them is not a walkthrough."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_form_marking_nothing_required_still_points_at_a_field(self):
        """Measured on the vendor form, whose arch requires nothing visible."""
        action = self.env["ir.actions.act_window"].create({
            "name": "Vendors, near enough",
            "res_model": "res.partner",
        })
        menu = self.env["ir.ui.menu"].create({
            "name": "Vendors, near enough",
            "action": "ir.actions.act_window,%d" % action.id,
        })
        steps = self.builder._creation_steps(menu)
        fields = [step["trigger"] for step in steps if '[name="' in step["trigger"]]
        self.assertTrue(fields, "the user was shown two buttons and no work")
        self.assertIn('[name="%s"]' % self.env["res.partner"]._rec_name, fields[0])


class TestTheButtonNeedsARecord(TransactionCase):
    """A header button with no record open points at nothing."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_screen_with_no_new_button_gets_no_forward_button(self):
        """An action opening straight into a form creates nothing."""
        action = self.env["ir.actions.act_window"].create({
            "name": "Straight into a form",
            "res_model": "res.users",
            "view_mode": "form",
        })
        menu = self.env["ir.ui.menu"].create({
            "name": "Straight into a form",
            "action": "ir.actions.act_window,%d" % action.id,
        })
        steps = self.builder._stage_steps(
            {"menu": menu, "goal": "", "create": True, "fields": []},
            opening=True)
        self.assertFalse([s for s in steps if s["trigger"].startswith("button[name=")],
                         "a button step with no record to press it on")


class TestTheNamingFieldIsSomethingYouCanType(TransactionCase):
    """The record's name, only where the form really offers it."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_read_only_node_is_not_offered(self):
        node = etree.fromstring('<field name="name" readonly="1"/>')
        self.assertFalse(self.builder._fillable_here(
            node, self.env["res.users"], self.env.ref("base.action_res_users")))

    def test_a_column_of_an_embedded_table_is_not_a_field_of_this_form(self):
        """An invoice draws its number again as a column of its lines."""
        arch = etree.fromstring(
            '<form><field name="line_ids"><list>'
            '<field name="name"/></list></field></form>')
        node = [n for n in arch.iter("field") if n.get("name") == "name"][0]
        self.assertFalse(self.builder._fillable_here(
            node, self.env["res.users"], self.env.ref("base.action_res_users")))

    def test_a_plain_node_is_offered(self):
        arch = etree.fromstring('<form><group><field name="name"/></group></form>')
        node = [n for n in arch.iter("field") if n.get("name") == "name"][0]
        self.assertTrue(self.builder._fillable_here(
            node, self.env["res.users"], self.env.ref("base.action_res_users")))


class TestTheFloorUnderACreationStage(TransactionCase):
    """Something to fill, on a form that neither requires nor names anything."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_decorative_widget_is_not_the_field_to_offer(self):
        node = etree.fromstring('<field name="name" widget="priority"/>')
        self.assertFalse(self.builder._fillable_here(
            node, self.env["res.users"], self.env.ref("base.action_res_users"))
            is None)
        action = self.env.ref("base.action_res_users")
        offered = self.builder._first_fillable_field(self.env["res.users"], action)
        self.assertTrue(offered, "a user form has boxes to fill")
        self.assertNotIn(offered[0][0], ("priority", "sequence"))


class TestAConditionIsReadRatherThanFeared(TransactionCase):
    """A condition about a record that does not exist yet can still be settled."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]
        self.action = self.env.ref("base.action_res_users")
        self.users = self.env["res.users"]

    def test_a_condition_that_cannot_be_settled_stays_unsettled(self):
        self.assertIsNone(self.builder._condition_holds(
            "something_that_is_not_a_field", self.users, self.action))

    def test_an_empty_condition_does_not_hold(self):
        self.assertIs(self.builder._condition_holds("", self.users, self.action),
                      False)

    def test_a_field_hidden_by_a_condition_that_is_false_is_still_on_the_page(self):
        """The blanket rule dropped these, and a vendor bill lost every field."""
        arch = etree.fromstring(
            '<form><field name="login" invisible="active == False"/></form>')
        node = [n for n in arch.iter("field")][0]
        self.assertTrue(self.builder._on_the_open_page(node, self.users, self.action))

    def test_a_field_hidden_by_a_condition_that_holds_is_not(self):
        arch = etree.fromstring(
            '<form><field name="login" invisible="active"/></form>')
        node = [n for n in arch.iter("field")][0]
        self.assertFalse(self.builder._on_the_open_page(node, self.users, self.action))

    def test_with_nothing_to_evaluate_against_it_still_refuses(self):
        arch = etree.fromstring(
            '<form><field name="login" invisible="active == False"/></form>')
        node = [n for n in arch.iter("field")][0]
        self.assertFalse(self.builder._on_the_open_page(node))


class TestAComputedConditionIsRead(TransactionCase):
    """Half of what a modifier tests is computed, and has no default."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_a_condition_on_a_computed_field_is_settled_not_assumed(self):
        """``hide_reservation_method`` is how an operation type hides a field.

        Read from defaults it comes back False and the field looks visible;
        read from a new record the compute runs and says otherwise. Built here
        on a model that ships with base, since a module upgrade loads only the
        modules under test.
        """
        users = self.env["res.users"]
        action = self.env.ref("base.action_res_users")
        # share is computed from the user's groups and is False for a new one.
        self.assertIs(self.builder._condition_holds("share", users, action), False)
        self.assertIs(self.builder._condition_holds("not share", users, action), True)

    def test_a_new_record_is_never_written(self):
        before = self.env["res.users"].search_count([])
        self.builder._condition_holds(
            "share", self.env["res.users"], self.env.ref("base.action_res_users"))
        self.assertEqual(self.env["res.users"].search_count([]), before)


class TestRequiredIsNotSymmetricWithHidden(TransactionCase):
    """The two costs are different, so the two readings are treated differently."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def test_both_readings_are_available(self):
        users = self.env["res.users"]
        action = self.env.ref("base.action_res_users")
        for reading in ("record", "defaults"):
            self.assertIn(
                self.builder._condition_holds("share", users, action, reading),
                (True, False, None),
                "a reading that cannot answer at all is worse than either")

    def test_a_field_required_under_either_reading_is_pointed_at(self):
        """A missing required field is a record that will not save.

        An extra field step is a click on a box already on the screen, so the
        rule follows the cost rather than the tidier symmetry. The form is
        this test's own: a vendor bill is where this was measured, and a
        module upgrade does not load account.
        """
        view = self.env["ir.ui.view"].create({
            "name": "a login required on a condition",
            "model": "res.users",
            "type": "form",
            "arch": '<form><field name="login" required="share"/></form>',
        })
        action = self.env["ir.actions.act_window"].create({
            "name": "users, through that form",
            "res_model": "res.users",
            "view_id": view.id,
            "view_mode": "form",
        })

        def only_defaults_say_yes(this, condition, model, act, reading="record"):
            return reading == "defaults"

        with patch.object(type(self.builder), "_condition_holds",
                          only_defaults_say_yes):
            shown, demanded = self.builder._form_fields(
                self.env["res.users"], action)
        self.assertIn("login", demanded,
                      "the reading that said yes was never asked for")

    def test_a_field_neither_reading_requires_is_left_alone(self):
        view = self.env["ir.ui.view"].create({
            "name": "a login required on a condition",
            "model": "res.users",
            "type": "form",
            "arch": '<form><field name="login" required="share"/></form>',
        })
        action = self.env["ir.actions.act_window"].create({
            "name": "users, through that form",
            "res_model": "res.users",
            "view_id": view.id,
            "view_mode": "form",
        })

        def nobody_says_yes(this, condition, model, act, reading="record"):
            return False

        with patch.object(type(self.builder), "_condition_holds", nobody_says_yes):
            shown, demanded = self.builder._form_fields(
                self.env["res.users"], action)
        self.assertNotIn("login", demanded)
        self.assertIn("login", shown, "it is still a field on the page")


class TestAStatusBarIsNotABoxToFill(TransactionCase):
    """A required field is not always something anybody types into."""

    def setUp(self):
        super().setUp()
        self.builder = self.env["tour.assistant.builder"]

    def _through(self, arch):
        view = self.env["ir.ui.view"].create({
            "name": "a form of this test's own", "model": "res.users",
            "type": "form", "arch": arch,
        })
        action = self.env["ir.actions.act_window"].create({
            "name": "users, through that form", "res_model": "res.users",
            "view_id": view.id, "view_mode": "form",
        })
        return self.builder._form_fields(self.env["res.users"], action)

    def test_a_status_bar_never_becomes_a_step(self):
        """account.payment marks state required, and the form draws it as one.

        "Fill in State" pointed at a status bar: not a box, and clicking it
        moves the record rather than accepting a value.
        """
        shown, dummy = self._through(
            '<form><field name="login" widget="statusbar"/>'
            '<field name="name"/></form>')
        self.assertNotIn("login", shown)
        self.assertIn("name", shown)

    def test_an_ordinary_field_is_untouched(self):
        shown, dummy = self._through('<form><field name="login"/></form>')
        self.assertIn("login", shown)

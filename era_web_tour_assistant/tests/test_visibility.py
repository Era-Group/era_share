# -*- coding: utf-8 -*-
"""Who is offered a walkthrough, which is a question about every screen in it.

A walkthrough that spans several apps is only as usable as its least reachable
stage. Being offered one you cannot finish is worse than being offered nothing:
you follow it, and it stops halfway with an access error.

The reachable set is passed in rather than discovered here. That is how the
module calls it — worked out once per question rather than once per tour — and
it is also the only way to test the rule itself rather than the permissions of
whichever user the test happens to run as.
"""

from odoo.tests.common import TransactionCase


class TestWhoIsOffered(TransactionCase):

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"].sudo()
        self.menus = self.env["ir.ui.menu"].search([], limit=3)
        self.assertEqual(len(self.menus), 3, "the fixture needs three menus")

    def _tour(self, suffix, **values):
        payload = {
            "name": "assistant_visibility_%s" % suffix,
            "url": "/odoo",
            "custom": True,
            "assistant_enabled": True,
            "assistant_generated": True,
        }
        payload.update(values)
        return self.tours.create(payload)

    def test_a_tour_naming_no_menus_is_offered_to_everyone(self):
        """Tours somebody recorded by hand say nothing about menus.

        Recorded, not generated: a generated walkthrough always records the
        menus it walks, so an empty list means they were deleted with the app
        they belonged to — and it is withheld. The fixture said generated while
        the docstring said recorded, and the two only diverged once that
        mattered.
        """
        tour = self._tour("open", assistant_generated=False)
        self.assertTrue(tour._assistant_is_visible_to_user(set()))

    def test_a_tour_whose_menus_are_all_reachable_is_offered(self):
        tour = self._tour("reachable", assistant_menu_ids=[(6, 0, self.menus.ids)])
        self.assertTrue(tour._assistant_is_visible_to_user(set(self.menus.ids)))

    def test_one_unreachable_stage_withdraws_the_whole_tour(self):
        """The failure this exists to prevent: help that stops halfway."""
        tour = self._tour("mixed", assistant_menu_ids=[(6, 0, self.menus.ids)])
        all_but_one = set(self.menus.ids) - {self.menus[-1].id}
        self.assertFalse(tour._assistant_is_visible_to_user(all_but_one))

    def test_reaching_more_than_the_tour_needs_is_fine(self):
        tour = self._tour("subset", assistant_menu_ids=[(6, 0, self.menus[:1].ids)])
        self.assertTrue(tour._assistant_is_visible_to_user(set(self.menus.ids)))

    def test_the_group_gate_refuses_on_its_own(self):
        """Untouched for tours a person wrote: any of these groups will do."""
        nobody = self.env["res.groups"].create({"name": "Assistant test group"})
        tour = self._tour("grouped", assistant_group_ids=[(6, 0, nobody.ids)])
        self.assertFalse(tour._assistant_is_visible_to_user(set()))

    def test_a_group_the_user_holds_lets_the_tour_through(self):
        # Menus given, or the menu gate decides this before the group gate does.
        held = self.env.user.all_group_ids[:1]
        self.assertTrue(held, "the fixture needs the user to hold a group")
        tour = self._tour("held",
                          assistant_group_ids=[(6, 0, held.ids)],
                          assistant_menu_ids=[(6, 0, self.menus.ids)])
        self.assertTrue(
            tour._assistant_is_visible_to_user(set(self.menus.ids)))

    def test_both_gates_have_to_agree(self):
        """A tour can name reachable menus and still be the wrong audience."""
        nobody = self.env["res.groups"].create({"name": "Assistant test group 2"})
        tour = self._tour(
            "both",
            assistant_group_ids=[(6, 0, nobody.ids)],
            assistant_menu_ids=[(6, 0, self.menus.ids)],
        )
        self.assertFalse(tour._assistant_is_visible_to_user(set(self.menus.ids)))

    def test_the_reachable_set_is_worked_out_when_it_is_not_supplied(self):
        """Calling it one tour at a time still has to give the right answer."""
        tour = self._tour("computed", assistant_menu_ids=[(6, 0, self.menus.ids)])
        reachable = self.env["web_tour.tour"]._assistant_reachable_menu_ids()
        self.assertEqual(
            tour._assistant_is_visible_to_user(),
            set(self.menus.ids) <= reachable,
        )


class TestAWalkthroughWhoseMenusAreGone(TransactionCase):
    """Uninstalling an app deletes its menus, and takes the gate with them.

    A generated walkthrough records every menu it walks, and the gate withholds
    it from anybody who cannot reach all of them. Delete those menus — which is
    what uninstalling the app they belong to does — and the list is empty, the
    gate has nothing left to check, and the walkthrough is offered to everybody
    while every one of its steps aims at a menu that is not there.

    Three hundred questions could not find this: none of them uninstalled
    anything.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]

    def _tour(self, generated=True, menus=None):
        return self.tours.create({
            "name": "gone_%d" % (1 if generated else 2),
            "custom": True,
            "assistant_enabled": True,
            "assistant_generated": generated,
            "assistant_menu_ids": [(6, 0, menus or [])],
        })

    def test_a_generated_walkthrough_with_no_menus_left_is_withheld(self):
        tour = self._tour(generated=True, menus=[])
        self.assertFalse(tour._assistant_is_visible_to_user())

    def test_a_recorded_tour_with_no_menus_is_still_offered(self):
        """Its author said who it is for with the groups instead."""
        tour = self._tour(generated=False, menus=[])
        self.assertTrue(tour._assistant_is_visible_to_user())

    def test_a_generated_walkthrough_that_still_has_its_menus_is_offered(self):
        tour = self._tour(
            generated=True, menus=[self.env.ref("base.menu_administration").id])
        self.assertTrue(tour._assistant_is_visible_to_user())

    def test_it_disappears_from_the_library_too(self):
        tour = self._tour(generated=True, menus=[])
        listed = [row["name"] for row in self.tours.assistant_library()]
        self.assertNotIn(tour.name, listed)


class TestReachableComesFromWhatTheClientDraws(TransactionCase):
    """The menu tree and the navbar disagree, and the tree is the wrong source.

    hr_timesheet.timesheet_menu_activity_user is a child of the Timesheets app
    with an action of its own. It passes every check the tree can make — it
    exists, it is visible, it is not a section — and the web client does not
    put it in the navbar at all. Two walkthroughs aimed a step at it and waited
    for an element that was never coming.

    load_menus is what the client builds the navbar from, so that is what a
    step may aim at.
    """

    def setUp(self):
        super().setUp()
        self.tours = self.env["web_tour.tour"]

    def test_the_payload_is_what_is_asked(self):
        reachable = self.tours._assistant_reachable_menu_ids()
        payload = self.env["ir.ui.menu"].load_menus(False)
        self.assertEqual(
            reachable, {key for key in payload if isinstance(key, int)})

    def test_a_menu_the_client_leaves_out_is_not_reachable(self):
        menu = self.env.ref(
            "hr_timesheet.timesheet_menu_activity_user",
            raise_if_not_found=False)
        if not menu:
            self.skipTest("hr_timesheet is not installed here")
        self.assertNotIn(menu.id, self.tours._assistant_reachable_menu_ids())

    def test_an_ordinary_menu_still_is(self):
        """The narrower source must not withhold what the navbar does draw."""
        menu = self.env.ref("base.menu_administration")
        self.assertIn(menu.id, self.tours._assistant_reachable_menu_ids())

    def test_enough_menus_survive_to_answer_with(self):
        self.assertGreater(
            len(self.tours._assistant_reachable_menu_ids()), 50,
            "a source that lists almost nothing would answer almost nothing")


class TestOnboardingGivenBack(TransactionCase):
    """A walkthrough deleted mid-way left the user's preference inverted.

    Starting one turns Odoo's onboarding on, because a manual tour does not
    survive the redirect to its starting page otherwise, and the preference is
    handed back when it ends. Delete the walkthrough while somebody is part-way
    through — which the nightly sweep now does on purpose — and the pointer is
    cleared by the database while nothing hands the preference back. Onboarding
    tours then interrupt that person for good, with nothing to connect it to
    having asked a question.
    """

    def setUp(self):
        super().setUp()
        self.users = self.env["res.users"]
        self.tour = self.env["web_tour.tour"].sudo().create({
            "name": "orphan_probe", "custom": True, "assistant_generated": True,
        })
        self.walker = self.env.user.sudo()

    def test_the_preference_comes_back_when_the_tour_vanishes(self):
        user = self.walker
        user.tour_enabled = False
        user._assistant_start_tour(self.tour)
        self.assertTrue(user.tour_enabled, "suspended for the walkthrough")

        self.tour.unlink()
        user.invalidate_recordset(["assistant_pending_tour_id"])
        self.assertFalse(user.assistant_pending_tour_id)

        # Asserted on the preference rather than on how many records the
        # sweep touched: the preference is the behaviour, the count is an
        # implementation detail, and other users on the database have their
        # own reasons to be in the list.
        self.users._assistant_restore_orphans()
        user.invalidate_recordset(["tour_enabled", "assistant_onboarding_suspended"])
        self.assertFalse(user.tour_enabled, "and handed back afterwards")
        self.assertFalse(user.assistant_onboarding_suspended)

    def test_somebody_still_walking_is_left_alone(self):
        user = self.walker
        user.tour_enabled = False
        user._assistant_start_tour(self.tour)
        self.users._assistant_restore_orphans()
        user.invalidate_recordset(["tour_enabled"])
        self.assertTrue(user.tour_enabled, "their walkthrough is still running")

    def test_somebody_who_simply_wants_onboarding_is_untouched(self):
        """The suspended flag is what tells the two apart."""
        user = self.walker
        user.write({
            "tour_enabled": True,
            "assistant_onboarding_suspended": False,
            "assistant_pending_tour_id": False,
        })
        self.users._assistant_restore_orphans()
        user.invalidate_recordset(["tour_enabled"])
        self.assertTrue(user.tour_enabled)

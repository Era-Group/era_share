# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    assistant_pending_tour_id = fields.Many2one(
        "web_tour.tour",
        string="Assistant Tour in Progress",
        copy=False,
        help="The tour the user asked the assistant for and has not finished. "
             "While it is set, Odoo's own onboarding tours stay out of the "
             "way so they cannot interrupt it.",
    )
    assistant_onboarding_restore = fields.Boolean(
        string="Onboarding Setting to Restore",
        copy=False,
        help="What the Onboarding setting was before the assistant had to "
             "turn it on. It is put back when the tour finishes.",
    )

    assistant_onboarding_suspended = fields.Boolean(
        string="Onboarding Suspended By The Assistant",
        default=False,
        copy=False,
        help="Set while the assistant has turned Odoo's onboarding tours on so "
             "a walkthrough survives a redirect. It is what says the stored "
             "preference is owed back — without it, a user whose walkthrough "
             "vanished mid-way could not be told apart from one who wants "
             "onboarding on.",
    )

    def _assistant_start_tour(self, tour):
        """Prepare this user's session to actually receive ``tour``.

        The web client throws a manual tour away on redirect unless the user
        has onboarding switched on, so a tour someone explicitly asked for
        never survives the jump to its starting page. Turning the setting on
        is the only way through; remembering the old value is how the user
        gets their preference back afterwards.
        """
        self.ensure_one()
        # sudo: these three fields are the assistant's own bookkeeping on the
        # user record, which users have no write access to.
        user = self.sudo()
        if not user.assistant_pending_tour_id:
            user.assistant_onboarding_restore = user.tour_enabled
        user.write({
            "assistant_pending_tour_id": tour.id,
            "assistant_onboarding_suspended": True,
            "tour_enabled": True,
        })

    def _assistant_continue_tour(self, finished, following):
        """Hand the bookkeeping from one stage of a walkthrough to the next.

        Deliberately not finish-then-start: restoring the onboarding
        preference between two stages would leave a window in which Odoo's own
        onboarding tour is free to start over the top of a walkthrough that has
        not ended, which is the exact interruption ``_assistant_start_tour``
        exists to prevent. ``assistant_onboarding_restore`` is left untouched
        so the value put back at the end is still the user's own.
        """
        self.ensure_one()
        if self.assistant_pending_tour_id != finished:
            return False
        # sudo: same bookkeeping fields as above.
        self.sudo().assistant_pending_tour_id = following.id
        return True

    def _assistant_finish_tour(self, tour):
        """Put the user's onboarding preference back once ``tour`` is done."""
        self.ensure_one()
        if self.assistant_pending_tour_id != tour:
            return False
        # sudo: same bookkeeping fields as above.
        self.sudo().write({
            "assistant_pending_tour_id": False,
            "assistant_onboarding_suspended": False,
            "tour_enabled": self.assistant_onboarding_restore,
        })
        return True

    @api.model
    def _assistant_restore_orphans(self):
        """Give back the onboarding preference of anybody whose tour vanished.

        Starting a walkthrough turns Odoo's own onboarding on, because a manual
        tour does not survive the redirect to its starting page otherwise, and
        the preference is handed back when the walkthrough ends. If the
        walkthrough is deleted while somebody is part-way through it — which
        the nightly sweep of walkthroughs an older builder wrote now does on
        purpose — the pointer is cleared by the database and nothing hands the
        preference back. The user is left with onboarding tours interrupting
        them for good, and no way to connect that to having asked a question.

        The suspended flag is what tells that user apart from one who simply
        wants onboarding on.
        """
        # active_test=False, or a user who was deactivated while a
        # walkthrough was open never gets their preference back — and neither
        # does __system__, which is how this was found: a search that skipped
        # inactive users skipped the very case the sweep exists for.
        orphans = self.sudo().with_context(active_test=False).search([
            ("assistant_onboarding_suspended", "=", True),
            ("assistant_pending_tour_id", "=", False),
        ])
        for user in orphans:
            user.write({
                "assistant_onboarding_suspended": False,
                "tour_enabled": user.assistant_onboarding_restore,
            })
        return len(orphans)

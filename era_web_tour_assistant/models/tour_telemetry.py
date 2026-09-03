# -*- coding: utf-8 -*-
"""What this instance can report back, and what it must never send.

Every fault found in a walkthrough so far came from the shape of a view — a
field inside a table, one hidden on a condition, a column of a list. Views
differ from one client's database to the next, and no amount of testing on one
database covers them. The instances themselves are the only instrument that
does.

**The question a user typed is never sent.** It is the one thing that can carry
a customer's name, an amount, a person — and it is the one thing that is not
needed. What improves the module is structural: how many questions were
answered, how many walkthroughs were finished, which menus the failing ones
crossed, how long planning took. Numbers and menu identifiers, both of which
this module wrote itself.

That is a promise a comment cannot keep, so ``test_telemetry.py`` asserts it: a
question with a distinctive string in it must not appear anywhere in the
payload, at any depth.

Off unless the customer turns it on, and what it sends is readable from the
same screen that turns it on.
"""

import json
import logging
import urllib.request

from odoo import api, models

_logger = logging.getLogger(__name__)

SEND_TIMEOUT = 20


class TourTelemetry(models.AbstractModel):
    _name = "tour.assistant.telemetry"
    _description = "Tour Assistant Telemetry"

    @api.model
    def _enabled(self):
        setting = self.env["ir.config_parameter"].sudo()
        return (
            setting.get_param("era_web_tour_assistant.telemetry", "False") == "True"
            and bool(setting.get_param("era_web_tour_assistant.telemetry_url"))
        )

    @api.model
    def _snapshot(self):
        """Counts and menu identifiers. No text a person wrote.

        Menu external ids are this module's own vocabulary — ``mrp.menu_mrp_root``
        says which screen a walkthrough crossed and nothing about the business
        on it — so they travel. Everything else here is a number.
        """
        requests = self.env["tour.assistant.request"].sudo().search([])
        tours = self.env["web_tour.tour"].sudo().search([
            ("assistant_generated", "=", True),
            ("assistant_first_stage_id", "=", False),
        ])

        answered = requests.filtered(lambda r: r.state in ("matched", "ready"))
        queued = requests.filtered(lambda r: r.state == "queued")
        built = [r.build_seconds for r in requests if r.build_seconds]
        built.sort()

        # Where the failures cluster. A walkthrough somebody reported is
        # described by the screens it crosses, which is what we would need to
        # reproduce it — never by the question that produced it.
        reported = []
        for request in requests.filtered(lambda r: r.reported_count):
            if not request.tour_id:
                continue
            reported.append({
                "menus": sorted(self._menu_xmlids(request.tour_id)),
                "reports": request.reported_count,
                "asked": request.ask_count,
                "completed": request.completed_count,
            })

        return {
            "instance": self.env.cr.dbname,
            "module": self.env["ir.module.module"].sudo().search(
                [("name", "=", "era_web_tour_assistant")], limit=1).latest_version or "",
            "builder": self._builder_version(),
            "questions": {
                "asked": sum(requests.mapped("ask_count")),
                "distinct": len(requests),
                "answered": len(answered),
                "queued": len(queued),
            },
            "walkthroughs": {
                "built": len(tours),
                "steps": sum(
                    len(stage.step_ids)
                    for tour in tours
                    for stage in self.env["web_tour.tour"]._assistant_chain(tour)
                ),
                "multi_stage": len(
                    tours.filtered(lambda t: t.assistant_next_stage_id)),
            },
            "completion": {
                "started": sum(answered.mapped("ask_count")),
                "finished": sum(requests.mapped("completed_count")),
            },
            "reported": reported,
            "build_seconds": {
                "count": len(built),
                "median": round(built[len(built) // 2], 1) if built else 0,
                "slowest": round(built[-1], 1) if built else 0,
            },
            "menus_offered": len(
                self.env["web_tour.tour"]._assistant_reachable_menu_ids() or []),
        }

    @api.model
    def _builder_version(self):
        from . import tour_builder
        return tour_builder.BUILDER_VERSION

    @api.model
    def _menu_xmlids(self, tour):
        """External ids of the menus a walkthrough crosses, chain included."""
        found = set()
        for stage in self.env["web_tour.tour"]._assistant_chain(tour):
            for menu in stage.assistant_menu_ids:
                data = self.env["ir.model.data"].sudo().search([
                    ("model", "=", "ir.ui.menu"), ("res_id", "=", menu.id),
                ], limit=1)
                if data:
                    found.add("%s.%s" % (data.module, data.name))
        return found

    @api.model
    def send(self):
        """Post the snapshot, if this database was told it may.

        Never raises: a report that fails is worth nothing and a walkthrough
        that stops working because a reporting endpoint moved is worth less
        than nothing.
        """
        if not self._enabled():
            return False
        url = self.env["ir.config_parameter"].sudo().get_param(
            "era_web_tour_assistant.telemetry_url")
        try:
            payload = json.dumps(self._snapshot()).encode()
            request = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(request, timeout=SEND_TIMEOUT).close()
        except Exception:
            _logger.warning(
                "Tour Assistant: could not report to %s", url, exc_info=True)
            return False
        return True

# -*- coding: utf-8 -*-
"""The switch that decides whether this instance reports anything.

It sat in System Parameters, which is a place an administrator of a client's
database has no reason to visit and every reason to distrust. A switch nobody
can find is not consent, and a customer who discovers reporting after the fact
is right to be angry about it however little it sends.

So it is on the Settings screen beside the module's other options, and what it
sends is written next to it rather than in a manual — see ``tour_telemetry``,
which is also where the tests live that keep the promise true.
"""

from odoo import _, api, fields, models

from . import tour_builder
from .tour_request import DEFAULT_MATCH_THRESHOLD


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tour_assistant_telemetry = fields.Boolean(
        string="Share Usage With The Publisher",
        config_parameter="era_web_tour_assistant.telemetry",
        help="Send counts of how the assistant is doing, so faults that only "
             "appear on some databases can be found and fixed. Questions your "
             "staff type are never sent.",
    )
    tour_assistant_telemetry_url = fields.Char(
        string="Reporting Address",
        config_parameter="era_web_tour_assistant.telemetry_url",
        help="Where the report is posted. Nothing is sent until both this and "
             "the switch above are set.",
    )
    # A config_parameter field with no default reads as False on a database
    # where the parameter was never written — which is every database on the
    # day it is installed. The screen then shows the builder switched off
    # while the code has it on, and the first administrator to open Settings
    # and press Save writes that back as the truth, turning off the one thing
    # answering anybody. These defaults are the values the models already fall
    # back to, so the screen and the code agree from the first render.
    tour_assistant_match_threshold = fields.Float(
        string="Answer Confidence",
        default=DEFAULT_MATCH_THRESHOLD,
        config_parameter="era_web_tour_assistant.match_threshold",
        help="How well a question must agree with a walkthrough before it is "
             "answered with it. Higher asks for more agreement and queues more "
             "questions instead of risking a wrong answer.",
    )
    tour_assistant_generate = fields.Boolean(
        string="Build Walkthroughs Automatically",
        default=True,
        config_parameter="era_web_tour_assistant.generate_tours",
        help="Work out a walkthrough for a question no recorded tour answers. "
             "Off, unanswered questions only collect in the queue.",
    )

    @api.model
    def tour_assistant_telemetry_preview(self):
        """Exactly what would be sent, from this database, right now.

        A description of what is collected is a claim; the payload is the fact.
        An administrator deciding whether to turn this on should be able to
        read the thing itself, which is also the strongest argument that no
        question text is in it.
        """
        return self.env["tour.assistant.telemetry"]._snapshot()

    tour_assistant_build = fields.Char(
        string="Walkthrough Builder",
        compute="_compute_tour_assistant_build",
        help="Which build of the walkthrough builder this database is running, "
             "and how many stored walkthroughs an older one wrote. Anything "
             "older is rebuilt on its next use.",
    )

    @api.depends_context("uid")
    def _compute_tour_assistant_build(self):
        """Answer "is this up to date?" with a number rather than a feeling.

        A database that has not been upgraded looks exactly like one that has,
        right up until somebody walks a walkthrough written by the older
        builder and meets a fault that was fixed weeks ago. Two containers of
        this module ran three versions apart and nothing on any screen said
        so.
        """
        # sudo: counting generated walkthroughs is not a business record, and
        # an administrator reading their own Settings page should not need
        # rights over web_tour.tour to see whether their install is current.
        tours = self.env["web_tour.tour"].sudo()
        current = tour_builder.BUILDER_VERSION
        stale = tours.search_count([
            ("assistant_generated", "=", True),
            ("assistant_builder_version", "<", current),
        ])
        total = tours.search_count([("assistant_generated", "=", True)])
        for record in self:
            if stale:
                record.tour_assistant_build = _(
                    "Build %(current)s — %(stale)s of %(total)s walkthroughs "
                    "were written by an older one and will be rebuilt.",
                    current=current, stale=stale, total=total)
            else:
                record.tour_assistant_build = _(
                    "Build %(current)s — all %(total)s walkthroughs were "
                    "written by it.", current=current, total=total)

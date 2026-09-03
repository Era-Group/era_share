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

from odoo import api, fields, models

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


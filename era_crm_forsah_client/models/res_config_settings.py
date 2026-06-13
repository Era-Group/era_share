# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    forsah_business_description = fields.Text(
        related="company_id.forsah_business_description",
        readonly=False,
        help="Free-text description of the company's business, used by the AI "
             "to score tender relevance.")

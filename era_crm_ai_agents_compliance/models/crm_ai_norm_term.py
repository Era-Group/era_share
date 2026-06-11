# -*- coding: utf-8 -*-
"""Manager-editable cultural-norms vocabulary (No-Hardcoded-Policy rule).

Replaces the hardcoded greeting / informal-opener / honorific lists in
services/norms.py with rows a compliance manager can edit. Seeded on install
from the module defaults (post_init_hook) so out-of-the-box behavior is
unchanged. The norms engine reads the active terms at runtime; if the table is
empty (or no env), it falls back to the in-code defaults.
"""
from odoo import fields, models


class CrmAiNormTerm(models.Model):
    _name = "crm.ai.norm.term"
    _description = "CRM AI Cultural-Norm Vocabulary"
    _order = "category, text"

    category = fields.Selection(
        selection=[
            ("greeting", "Greeting"),
            ("informal_opener", "Informal opener"),
            ("honorific", "Honorific"),
        ],
        string="Category", required=True, index=True,
    )
    text = fields.Char(string="Term", required=True)
    lang = fields.Selection(
        selection=[("ar", "Arabic"), ("en", "English"), ("any", "Any")],
        string="Language", default="any",
    )
    active = fields.Boolean(string="Active", default=True)

    _sql_constraints = [
        ("uniq_category_text", "unique(category, text)",
         "This term already exists in that category."),
    ]

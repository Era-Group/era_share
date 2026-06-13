# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmEtimadTag(models.Model):
    _name = "crm.etimad.tag.client"
    _description = "Etimad Tender Tag"
    _order = "name"

    name = fields.Char("Name", required=True)

    _sql_constraints = [
        ('name_uniq', 'unique(name)', "An Etimad tag with this name already exists."),
    ]


class CrmForsahTag(models.Model):
    _name = "crm.forsah.tag.client"
    _description = "Forsah Tender Tag"
    _order = "name"

    name = fields.Char("Name", required=True)

    _sql_constraints = [
        ('name_uniq', 'unique(name)', "A Forsah tag with this name already exists."),
    ]

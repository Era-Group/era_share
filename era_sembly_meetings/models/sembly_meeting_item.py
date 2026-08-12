# -*- coding: utf-8 -*-
"""One row per structured item Sembly extracted from a meeting.

``get_meeting`` returns tasks, decisions, issues, risks, requirements,
highlights and noteworthy details. They carry no user state, so a re-sync
simply deletes and recreates the MCP-sourced rows for that meeting.
"""
from odoo import fields, models

ITEM_TYPES = [
    ('task', "مهمة / Task"),
    ('decision', "قرار / Decision"),
    ('issue', "مشكلة / Issue"),
    ('risk', "مخاطرة / Risk"),
    ('requirement', "متطلب / Requirement"),
    ('highlight', "أبرز النقاط / Highlight"),
    ('noteworthy', "تفاصيل جديرة بالملاحظة / Noteworthy"),
]

# get_meeting key -> item_type. Order matters only for display grouping.
MCP_ITEM_KEYS = [
    ('tasks', 'task'),
    ('decisions', 'decision'),
    ('issues', 'issue'),
    ('risks', 'risk'),
    ('requirements', 'requirement'),
    ('highlights', 'highlight'),
    ('noteworthy_details', 'noteworthy'),
]


class SemblyMeetingItem(models.Model):
    _name = 'sembly.meeting.item'
    _description = "Sembly Meeting Item"
    _order = 'meeting_id desc, item_type, id'

    meeting_id = fields.Many2one(
        'sembly.meeting', string="الاجتماع", required=True,
        ondelete='cascade', index=True)
    item_type = fields.Selection(ITEM_TYPES, string="النوع", required=True, index=True)
    name = fields.Char(string="العنوان", required=True)
    description = fields.Text(string="التفاصيل")

    # Tasks only — Sembly leaves these null for the other item types.
    assigned_to = fields.Char(string="مُسند إلى")
    assigned_by = fields.Char(string="مُسند من")
    due_date = fields.Date(string="تاريخ الاستحقاق")
    raw_timing = fields.Char(
        string="التوقيت (نص)",
        help="Sembly's free-text timing, e.g. 'next week'. Kept verbatim "
             "because it is often not a parseable date.")

    sembly_item_id = fields.Char(string="معرّف Sembly")
    item_url = fields.Char(string="الرابط")

    # Denormalised for filtering the items list by date without a join.
    meeting_date = fields.Datetime(
        related='meeting_id.started_at', store=True, string="تاريخ الاجتماع")

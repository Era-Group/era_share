# -*- coding: utf-8 -*-
"""A read-only dialog for one piece of text.

WHY A TRANSIENT MODEL rather than an act_window back onto sembly.meeting:
returning an action whose res_model and res_id are the record you are already
looking at makes Odoo push a new breadcrumb over the current form instead of
opening a modal — ``target: 'new'`` is not honoured for that case. The result
was the whole page being replaced by the dialog's contents, complete with the
app's menu bar and a "New" button.

A transient of its own has no such ambiguity: it is always a dialog, it carries
nothing but the text being shown, and it keeps the originals one click away
without adding a field to the meeting.
"""
from odoo import fields, models


class SemblyTextDialog(models.TransientModel):
    _name = 'sembly.text.dialog'
    _description = "Sembly Text Viewer"

    meeting_id = fields.Many2one('sembly.meeting', string="الاجتماع", readonly=True)
    title = fields.Char(string="العنوان", readonly=True)
    note = fields.Char(string="ملاحظة", readonly=True)
    link = fields.Char(string="الرابط", readonly=True)
    body = fields.Html(string="النص", readonly=True, sanitize=False)

# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import ValidationError


class UmrahGroupMember(models.Model):
    _name = "umrah.group.member"
    _description = "Umrah Group Member"
    _rec_name = "pilgrim_id"

    pilgrim_id = fields.Many2one(
        "umrah.pilgrim",
        string="Pilgrim",
        required=True
    )

    group_id = fields.Many2one(
        "umrah.group",
        string="Group",
        required=True,
        ondelete="cascade"
    )

    trip_id = fields.Many2one(
        "umrah.trip",
        string="Trip",
        required=True
    )

    agent_id = fields.Many2one(
        "umrah.agent",
        string="Agent",
        related="group_id.agent_id",
        store=True
    )
    hotel_booking_ids = fields.Many2many(
        "umrah.hotel.booking",
        string="Hotel Bookings",
        compute="_compute_hotel_bookings"
    )

    def _compute_hotel_bookings(self):
        for rec in self:
            rec.hotel_booking_ids = self.env["umrah.hotel.booking"].search([
                ("group_ids", "in", rec.group_id.id)
            ])



    @api.onchange("group_id")
    def _onchange_group_id(self):
        if self.group_id:
            return {
                "domain": {
                    "trip_id": [("id", "in", self.group_id.trip_ids.ids)]
                }
            }
        else:
            return {
                "domain": {
                    "trip_id": []
                }
            }
# -*- coding: utf-8 -*-
"""Per-city, per-date prayer-time cache.

Reference data (not personal data): one row per (city, country, date) holding
the five daily prayer clock-times. The send-window engine reads it before any
API call; a daily warm-cron pre-fills it. AI users may read AND create/write it
(see ACL) so a cache miss at send time can be filled without sudo.
"""
from odoo import api, fields, models


class CrmAiPrayerCache(models.Model):
    _name = "crm.ai.prayer.cache"
    _description = "CRM AI Prayer-time Cache"
    _order = "date desc, city"

    city = fields.Char(string="City", required=True, index=True)
    country = fields.Char(string="Country", index=True)
    date = fields.Date(string="Date", required=True, index=True)
    fajr = fields.Char(string="Fajr")
    dhuhr = fields.Char(string="Dhuhr")
    asr = fields.Char(string="Asr")
    maghrib = fields.Char(string="Maghrib")
    isha = fields.Char(string="Isha")
    fetched_on = fields.Datetime(string="Fetched On")

    _sql_constraints = [
        ("uniq_city_country_date", "unique(city, country, date)",
         "A prayer-time cache row already exists for this city/country/date."),
    ]

    @api.model
    def cron_warm_prayer_cache(self):
        """ir.cron entry point — pre-fetch today + tomorrow for active cities."""
        from ..services.prayer_times import PrayerTimes
        return PrayerTimes(self.env).warm()

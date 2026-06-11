# -*- coding: utf-8 -*-
"""Prayer-time provider — real per-city, per-date times from the Aladhan API,
cached, with the approved hybrid fail-safe.

REGISTERED EGRESS (see rules.md "External Network Egress Registry"): the only
direct outbound call in the project. It sends ONLY city + country + date +
method — never any PII. No API key (Rule 03 by construction). Uses stdlib
``urllib`` (not ``requests``) so it stays clear of the anti-bypass import rule;
the one call is the allowlisted exception.

Fail-safe (confirmed): cache-today → live API → last-known-day cached times for
that city (+ audit warning) → return None (caller hard-blocks the send; never
send blind).
"""
import json
import logging
import urllib.parse
import urllib.request
from datetime import time

from odoo import fields

from .compliance_config import ComplianceConfig

_logger = logging.getLogger(__name__)

_API_URL = "https://api.aladhan.com/v1/timingsByCity"
_TIMEOUT = 4  # seconds
# (Aladhan key, cache column) in canonical order.
_PRAYERS = (
    ("Fajr", "fajr"),
    ("Dhuhr", "dhuhr"),
    ("Asr", "asr"),
    ("Maghrib", "maghrib"),
    ("Isha", "isha"),
)


def _parse_hhmm(value):
    """Aladhan returns e.g. '04:12' or '04:12 (+03)'. Return a datetime.time."""
    token = str(value or "").strip().split(" ")[0]
    h, m = token.split(":")
    return time(int(h), int(m))


class PrayerTimes:
    def __init__(self, env):
        self.env = env
        self.cfg = ComplianceConfig(env)
        self._memo = {}  # (city,country,date) -> {name: time} | None

    # ------------------------------------------------------------------
    def get_times(self, city, country, on_date):
        """Return {name: datetime.time} for the city/date, or None if no data is
        available anywhere (caller must block). Memoized per instance so a
        next_allowed_slot scan never re-hits the API/cache for the same key."""
        city = (city or self.cfg.s("default_city") or "Riyadh").strip()
        country = (country or self.cfg.s("default_country") or "SA").strip()
        key = (city.lower(), country.lower(), on_date)
        if key in self._memo:
            return self._memo[key]

        times = self._from_cache(city, country, on_date)
        if times is None:
            times = self._fetch_and_store(city, country, on_date)
        if times is None:
            times = self._last_known(city, country)
            if times is not None:
                self._warn_stale(city, country, on_date)

        self._memo[key] = times
        return times

    def warm(self):
        """Pre-fetch today + tomorrow for distinct active-partner cities + the
        default city. Returns the number of (city,date) entries fetched."""
        today = fields.Date.context_today(self.env["crm.ai.prayer.cache"])
        from datetime import timedelta
        dates = [today, today + timedelta(days=1)]
        cities = self._active_cities()
        count = 0
        for city, country in cities:
            for d in dates:
                if self._fetch_and_store(city, country, d) is not None:
                    count += 1
        _logger.info("cron_warm_prayer_cache: warmed %s prayer entries", count)
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _active_cities(self):
        """Distinct (city, country) among partners that have a city, plus the
        configured default. Capped to keep the cron bounded."""
        default = ((self.cfg.s("default_city") or "Riyadh"),
                   (self.cfg.s("default_country") or "SA"))
        seen = {(default[0].lower(), default[1].lower()): default}
        partners = self.env["res.partner"].search(
            [("city", "!=", False)], limit=500)
        for p in partners:
            city = (p.city or "").strip()
            country = (p.country_id.code or "") if p.country_id else ""
            if not city:
                continue
            k = (city.lower(), country.lower())
            seen.setdefault(k, (city, country))
        return list(seen.values())

    def _cache_model(self):
        return self.env["crm.ai.prayer.cache"]

    def _from_cache(self, city, country, on_date):
        row = self._cache_model().search([
            ("city", "=ilike", city), ("country", "=ilike", country),
            ("date", "=", on_date),
        ], limit=1)
        return self._row_to_times(row) if row else None

    def _last_known(self, city, country):
        row = self._cache_model().search([
            ("city", "=ilike", city), ("country", "=ilike", country),
        ], order="date desc", limit=1)
        return self._row_to_times(row) if row else None

    @staticmethod
    def _row_to_times(row):
        out = {}
        for name, col in _PRAYERS:
            val = getattr(row, col, None)
            if not val:
                return None
            out[name] = _parse_hhmm(val)
        return out

    def _fetch_and_store(self, city, country, on_date):
        """Live Aladhan call (city+date+method only). Returns the times dict and
        upserts the cache, or None on any failure (network, parse, HTTP)."""
        params = urllib.parse.urlencode({
            "city": city,
            "country": country,
            "method": self.cfg.i("prayer_method", 4),
            "date": on_date.strftime("%d-%m-%Y"),
        })
        url = "%s?%s" % (_API_URL, params)
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            timings = payload["data"]["timings"]
            times = {name: _parse_hhmm(timings[name]) for name, _col in _PRAYERS}
        except Exception as exc:  # network/parse/HTTP — fail-safe handles it
            _logger.warning("prayer API fetch failed for %s/%s %s: %s",
                            city, country, on_date, exc)
            return None
        self._store(city, country, on_date, times)
        return times

    def _store(self, city, country, on_date, times):
        vals = {
            "city": city, "country": country, "date": on_date,
            "fetched_on": fields.Datetime.now(),
        }
        for name, col in _PRAYERS:
            vals[col] = times[name].strftime("%H:%M")
        Cache = self._cache_model()
        existing = Cache.search([
            ("city", "=ilike", city), ("country", "=ilike", country),
            ("date", "=", on_date),
        ], limit=1)
        if existing:
            existing.write(vals)
        else:
            Cache.create(vals)

    def _warn_stale(self, city, country, on_date):
        self.env["crm.ai.audit.log"].log(
            "other",
            after={"event": "prayer_times_stale", "city": city,
                   "country": country, "date": str(on_date)},
        )

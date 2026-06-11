# -*- coding: utf-8 -*-
"""Configurability + prayer-time tests.

Covers: every toggle (incl. toggle-OFF = rule skipped), the editable norm
vocabulary, prayer-time correctness against a recorded real Jeddah payload, and
all three fail-safe paths (cache-hit, API-down → last-known-day, no-cache +
API-down → block). The live API is never called here — urllib is patched with a
recorded Aladhan response — so the suite is deterministic and offline.
"""
import json
from datetime import datetime, date
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

import pytz

from odoo.tests import TransactionCase, tagged

from odoo.addons.era_crm_ai_agents_compliance.services.send_window import SendWindow
from odoo.addons.era_crm_ai_agents_compliance.services.norms import CulturalNorms
from odoo.addons.era_crm_ai_agents_compliance.services.prayer_times import PrayerTimes

_TZ = "Asia/Riyadh"

# Real Aladhan response shape for Jeddah, 15-06-2026, method 4 (Umm al-Qura),
# verified live during the build. Used as a recorded fixture so the test is
# deterministic and needs no network.
_JEDDAH = {"Fajr": "04:12", "Dhuhr": "12:23", "Asr": "15:42",
           "Maghrib": "19:05", "Isha": "20:35"}
_JEDDAH_PAYLOAD = json.dumps({"data": {"timings": dict(
    _JEDDAH, Sunrise="05:38", Sunset="19:05", Imsak="04:02", Midnight="00:08",
)}}).encode("utf-8")


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _riyadh_utc(h, m, d=10):
    local = pytz.timezone(_TZ).localize(datetime(2026, 6, d, h, m))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


def _jeddah_utc(h, m):
    local = pytz.timezone(_TZ).localize(datetime(2026, 6, 15, h, m))
    return local.astimezone(pytz.utc).replace(tzinfo=None)


@tagged("post_install", "-at_install")
class TestToggles(TransactionCase):

    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"]
        self.ICP.set_param("era_crm_ai_agents_compliance.prayer_source", "fixed")

    def _set(self, key, val):
        self.ICP.set_param("era_crm_ai_agents_compliance." + key, val)

    def test_prayer_toggle_off_skips_rule(self):
        # Default: Dhuhr blocks. Toggle OFF: same instant is allowed.
        self.assertFalse(SendWindow(self.env).is_send_allowed(_riyadh_utc(12, 5), _TZ)[0])
        self._set("prayer_enabled", "0")
        self.assertTrue(SendWindow(self.env).is_send_allowed(_riyadh_utc(12, 5), _TZ)[0])

    def test_weekend_toggle(self):
        fri = _riyadh_utc(10, 0, d=12)  # 2026-06-12 is a Friday
        ok, reason = SendWindow(self.env).is_send_allowed(fri, _TZ)
        self.assertFalse(ok)
        self.assertEqual(reason, "weekend")
        self._set("weekend_enabled", "0")
        self.assertTrue(SendWindow(self.env).is_send_allowed(fri, _TZ)[0])

    def test_working_hours_toggle_and_value(self):
        # 22:00 blocked by default (ends 21:00); raise the end to allow it.
        self.assertFalse(SendWindow(self.env).is_send_allowed(_riyadh_utc(22, 0), _TZ)[0])
        self._set("working_end", "23:00")
        self.assertTrue(SendWindow(self.env).is_send_allowed(_riyadh_utc(22, 0), _TZ)[0])

    def test_send_window_master_toggle_off(self):
        self._set("send_window_enabled", "0")
        # Even at Dhuhr on a Friday night → allowed when master is OFF.
        self.assertTrue(SendWindow(self.env).is_send_allowed(_riyadh_utc(12, 5, d=12), _TZ)[0])

    def test_norms_master_toggle_off(self):
        bad = "ارسل الفلوس"
        self.assertFalse(CulturalNorms(self.env).check_norms(bad)[0])
        self._set("norms_enabled", "0")
        self.assertTrue(CulturalNorms(self.env).check_norms(bad)[0])

    def test_norms_per_check_toggle(self):
        # Greeting present, honorific missing → blocked; turn honorific check off.
        text = "السلام عليكم، نود المتابعة."
        self.assertFalse(CulturalNorms(self.env).check_norms(text)[0])
        self._set("norms_check_honorific", "0")
        self.assertTrue(CulturalNorms(self.env).check_norms(text)[0])


@tagged("post_install", "-at_install")
class TestNormVocab(TransactionCase):

    def test_vocab_seeded_on_install(self):
        self.assertTrue(self.env["crm.ai.norm.term"].search_count([]) > 0)

    def test_engine_uses_manager_added_term(self):
        text = "صباح النور أستاذ خالد"  # 'صباح النور' is NOT a default greeting
        self.assertFalse(CulturalNorms(self.env).check_norms(text)[0])  # missing greeting
        self.env["crm.ai.norm.term"].create({
            "category": "greeting", "text": "صباح النور", "lang": "ar"})
        self.assertTrue(CulturalNorms(self.env).check_norms(text)[0])  # now recognized


@tagged("post_install", "-at_install")
class TestPrayerTimes(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].set_param(
            "era_crm_ai_agents_compliance.prayer_source", "api")
        self.Cache = self.env["crm.ai.prayer.cache"]

    # -- correctness against the recorded real Jeddah payload -----------
    def test_live_fetch_matches_jeddah_and_caches(self):
        with patch("urllib.request.urlopen", return_value=_FakeResp(_JEDDAH_PAYLOAD)):
            times = PrayerTimes(self.env).get_times("Jeddah", "SA", date(2026, 6, 15))
        self.assertEqual({k: v.strftime("%H:%M") for k, v in times.items()}, _JEDDAH)
        # cache row was written
        row = self.Cache.search([("city", "=ilike", "Jeddah"),
                                 ("date", "=", date(2026, 6, 15))])
        self.assertTrue(row)
        self.assertEqual(row.dhuhr, "12:23")

    def test_prayer_block_uses_real_city_times(self):
        # Jeddah Dhuhr is 12:23 (not Riyadh's ~12:00): 12:30 local is inside the
        # 30-min Dhuhr block for Jeddah, proving per-city correctness.
        with patch("urllib.request.urlopen", return_value=_FakeResp(_JEDDAH_PAYLOAD)):
            ok, reason = SendWindow(self.env).is_send_allowed(
                _jeddah_utc(12, 30), _TZ, city="Jeddah", country="SA")
        self.assertFalse(ok)
        self.assertEqual(reason, "prayer time (Dhuhr)")

    # -- fail-safe 1: cache-hit (no network) ----------------------------
    def test_failsafe_cache_hit_no_api(self):
        self.Cache.create({
            "city": "Jeddah", "country": "SA", "date": date(2026, 6, 15),
            "fajr": "04:12", "dhuhr": "12:23", "asr": "15:42",
            "maghrib": "19:05", "isha": "20:35"})
        # urlopen must NOT be called on a cache hit.
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("API must not be called on cache hit")):
            times = PrayerTimes(self.env).get_times("Jeddah", "SA", date(2026, 6, 15))
        self.assertEqual(times["Dhuhr"].strftime("%H:%M"), "12:23")

    # -- fail-safe 2: API down → last-known-day (+ audit warning) -------
    def test_failsafe_api_down_uses_last_known(self):
        self.Cache.create({
            "city": "Jeddah", "country": "SA", "date": date(2026, 6, 1),
            "fajr": "04:13", "dhuhr": "12:22", "asr": "15:41",
            "maghrib": "19:03", "isha": "20:33"})
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            times = PrayerTimes(self.env).get_times("Jeddah", "SA", date(2026, 6, 15))
        self.assertIsNotNone(times)               # served stale, not blind
        self.assertEqual(times["Dhuhr"].strftime("%H:%M"), "12:22")
        stale = self.env["crm.ai.audit.log"].sudo().search_count(
            [("value_after", "ilike", "prayer_times_stale")])
        self.assertTrue(stale)

    # -- fail-safe 3: no cache + API down → block (never send blind) ----
    def test_failsafe_no_cache_api_down_blocks(self):
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            times = PrayerTimes(self.env).get_times("Nowhereville", "SA", date(2026, 6, 15))
            self.assertIsNone(times)
            ok, reason = SendWindow(self.env).is_send_allowed(
                _jeddah_utc(10, 0), _TZ, city="Nowhereville", country="SA")
        self.assertFalse(ok)
        self.assertEqual(reason, "prayer-time data unavailable")

    # -- egress carries ONLY city+country+date+method (no PII) ----------
    def test_api_url_carries_only_city_date_no_pii(self):
        captured = {}

        def fake_urlopen(url, timeout=None):
            captured["url"] = url
            return _FakeResp(_JEDDAH_PAYLOAD)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            PrayerTimes(self.env).get_times("Jeddah", "SA", date(2026, 6, 15))
        params = parse_qs(urlparse(captured["url"]).query)
        # Exactly these four keys — no partner name/phone/email/id ever sent.
        self.assertEqual(set(params), {"city", "country", "date", "method"})
        self.assertEqual(params["city"], ["Jeddah"])
        self.assertEqual(params["date"], ["15-06-2026"])
        self.assertEqual(params["method"], ["4"])

    # -- source toggle: 'fixed' makes ZERO API calls -------------------
    def test_source_fixed_makes_no_api_call(self):
        self.env["ir.config_parameter"].set_param(
            "era_crm_ai_agents_compliance.prayer_source", "fixed")
        with patch("urllib.request.urlopen",
                   side_effect=AssertionError("API must not be called when source=fixed")):
            ok, reason = SendWindow(self.env).is_send_allowed(
                _riyadh_utc(12, 5), _TZ, city="Jeddah", country="SA")
        # Fixed Dhuhr 12:00 (+30m block) → 12:05 blocked, with no network hit.
        self.assertFalse(ok)
        self.assertEqual(reason, "prayer time (Dhuhr)")

    # -- warm-cron populates the cache ---------------------------------
    def test_warm_cron_populates_cache(self):
        self.env["res.partner"].create({
            "name": "Jeddah Customer", "city": "Jeddah",
            "country_id": self.env.ref("base.sa").id})
        with patch("urllib.request.urlopen", return_value=_FakeResp(_JEDDAH_PAYLOAD)):
            n = self.env["crm.ai.prayer.cache"].cron_warm_prayer_cache()
        self.assertGreater(n, 0)
        self.assertTrue(self.Cache.search_count([("city", "=ilike", "Jeddah")]))

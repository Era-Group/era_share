# -*- coding: utf-8 -*-
"""Send-window engine — decides whether *now* is an allowed time to contact a
recipient, honouring Saudi cultural / religious norms.

Fully manager-configurable (No-Hardcoded-Policy rule): every rule reads its
setting from ``ComplianceConfig`` and has an enable/disable toggle — **a toggle
OFF means the rule is skipped entirely**. Defaults preserve the original KSA
behavior. Rules evaluated (in order) against the recipient's LOCAL wall-clock:
prayer times → Friday Jumu'ah → Ramadan-quiet → weekend → working hours.

Prayer clock-times come from ``_prayer_times_for`` — fixed configured times here;
task 1.10 swaps in the live per-city API + cache behind the same seam.

No DB writes, no sudo of its own (the only sudo is the read-only config read in
ComplianceConfig — approved elevation #7). Naive datetimes are treated as UTC;
``next_allowed_slot`` returns naive UTC.
"""
import logging
from datetime import datetime, timedelta

import pytz

from .compliance_config import ComplianceConfig

_logger = logging.getLogger(__name__)

try:
    from hijridate import Gregorian as _Gregorian
    _HIJRI_OK = True
except Exception:  # pragma: no cover
    _Gregorian = None
    _HIJRI_OK = False

_SCAN_STEP = timedelta(minutes=5)
_SCAN_LIMIT = timedelta(days=8)


class SendWindow:
    """Stateless evaluator. ``env`` lets it read manager settings (and, in 1.10,
    the prayer cache/API); without env it falls back to KSA defaults so it stays
    unit-testable in isolation."""

    def __init__(self, env=None):
        self.env = env
        self.cfg = ComplianceConfig(env)
        self._prayer = None  # lazy PrayerTimes provider (api source)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_send_allowed(self, now, partner_tz=None, city=None, country=None):
        cfg = self.cfg
        if not cfg.b("send_window_enabled"):
            return True, "allowed"

        local = self._to_local(now, partner_tz)
        t = local.time()

        if cfg.b("prayer_enabled"):
            times, available = self._prayer_times_for(local, city, country)
            if not available:
                # Fail-safe: no cached data for this city and the API is down.
                # Never send blind — block (the guard will defer/retry).
                return False, "prayer-time data unavailable"
            prayer = self._prayer_block_in(t, times)
            if prayer:
                return False, "prayer time (%s)" % prayer

        if cfg.b("jumuah_enabled") and local.weekday() == 4 and \
                self._in_window(t, cfg.time("jumuah_start"), cfg.time("jumuah_end")):
            return False, "Friday prayer (Jumu'ah)"

        if cfg.b("ramadan_enabled") and self._is_ramadan(local) and \
                self._in_window(t, cfg.time("ramadan_start"), cfg.time("ramadan_end")):
            return False, "Ramadan quiet hours"

        if cfg.b("weekend_enabled") and local.weekday() in cfg.weekend_days():
            return False, "weekend"

        if cfg.b("working_hours_enabled") and not \
                self._in_window(t, cfg.time("working_start"), cfg.time("working_end")):
            return False, "outside business hours"

        return True, "allowed"

    def next_allowed_slot(self, now, partner_tz=None, city=None, country=None):
        start = self._to_utc_aware(now).replace(second=0, microsecond=0)
        candidate = start
        deadline = start + _SCAN_LIMIT
        while candidate <= deadline:
            if self.is_send_allowed(candidate, partner_tz, city, country)[0]:
                return candidate.astimezone(pytz.utc).replace(tzinfo=None)
            candidate += _SCAN_STEP
        _logger.warning("send_window: no allowed slot within %s of %s", _SCAN_LIMIT, start)
        return deadline.astimezone(pytz.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # Prayer-times seam (1.10 replaces the body with API + cache + fail-safe)
    # ------------------------------------------------------------------
    def _prayer_times_for(self, local_dt, city, country):
        """Return ``(times: {name: time} | None, available: bool)``.

        - source 'fixed' (or no env): the configured fixed times — always
          available.
        - source 'api': real per-city times via the cache/API layer with the
          hybrid fail-safe. ``available`` is False only when there is no cached
          data for the city AND the live API is down — the caller then blocks.
        """
        if self.env is None or self.cfg.s("prayer_source") != "api":
            return self.cfg.fixed_prayer_times(), True
        times = self._prayer_provider().get_times(city, country, local_dt.date())
        if times is None:
            return None, False
        return times, True

    def _prayer_provider(self):
        if self._prayer is None:
            from .prayer_times import PrayerTimes
            self._prayer = PrayerTimes(self.env)
        return self._prayer

    def _prayer_block_in(self, t, times):
        block = self.cfg.i("prayer_block_minutes", 30)
        tmin = t.hour * 60 + t.minute
        for name, ptime in times.items():
            start = ptime.hour * 60 + ptime.minute
            if start <= tmin < start + block:
                return name
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    @staticmethod
    def _to_utc_aware(now):
        if now is None:
            now = datetime.utcnow()
        if now.tzinfo is None:
            return pytz.utc.localize(now)
        return now.astimezone(pytz.utc)

    def _to_local(self, now, partner_tz):
        tzname = partner_tz or self.cfg.s("default_tz") or "Asia/Riyadh"
        try:
            tz = pytz.timezone(tzname)
        except Exception:
            tz = pytz.timezone("Asia/Riyadh")
        return self._to_utc_aware(now).astimezone(tz)

    @staticmethod
    def _in_window(t, start, end):
        return start <= t < end

    @staticmethod
    def _is_ramadan(local_dt):
        if not _HIJRI_OK:
            return False
        try:
            return _Gregorian(local_dt.year, local_dt.month, local_dt.day).to_hijri().month == 9
        except Exception:  # pragma: no cover
            return False

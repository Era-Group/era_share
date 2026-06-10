# -*- coding: utf-8 -*-
"""Send-window engine — decides whether *now* is an allowed time to contact a
recipient, honouring Saudi cultural / religious norms (Rule: prayer-time and
Hijri-aware send windows).

Pure Python, evaluated entirely against the recipient's LOCAL wall-clock:
  - business hours,
  - the five daily prayers (each blocks a short window),
  - Friday Jumu'ah (a longer midday block),
  - Ramadan-quiet hours (afternoon → Iftar/Taraweeh), Ramadan detected from the
    Hijri calendar via the ``hijridate`` library.

No external calls, no DB writes, no sudo — so it is safe to call from any
salesperson-scoped agent. Prayer clock-times are approximate KSA defaults held
as overridable class constants; a later task can wire them to a precise per-day
source via the manager Settings page without changing this engine's shape.

Times in / out follow Odoo's convention: a naive datetime is treated as UTC, and
``next_allowed_slot`` returns a naive UTC datetime.
"""
import logging
from datetime import datetime, time, timedelta

import pytz

_logger = logging.getLogger(__name__)

# Ramadan detection is best-effort: if hijridate is unavailable we simply skip
# the Ramadan-quiet rule rather than fail the whole send-window check.
try:
    from hijridate import Gregorian as _Gregorian
    _HIJRI_OK = True
except Exception:  # pragma: no cover - depends on host packages
    _Gregorian = None
    _HIJRI_OK = False


class SendWindow:
    """Stateless send-window evaluator. ``env`` is accepted for forward
    compatibility (future config-driven overrides) but is not required, so the
    engine can be unit-tested in isolation: ``SendWindow().is_send_allowed(...)``.
    """

    # --- Overridable KSA defaults (Asia/Riyadh) --------------------------
    DEFAULT_TZ = "Asia/Riyadh"
    BUSINESS_START = time(9, 0)
    BUSINESS_END = time(21, 0)
    # Approximate daily prayer clock-times (local). Each blocks a window of
    # PRAYER_BLOCK_MINUTES starting at the time below.
    PRAYER_TIMES = {
        "Fajr": time(5, 0),
        "Dhuhr": time(12, 0),
        "Asr": time(15, 30),
        "Maghrib": time(18, 15),
        "Isha": time(19, 45),
    }
    PRAYER_BLOCK_MINUTES = 30
    # Friday Jumu'ah — a longer midday block (weekday() == 4 is Friday).
    FRIDAY_JUMUAH = (time(11, 30), time(13, 30))
    # Ramadan sensitivity: avoid the afternoon → Iftar/Taraweeh stretch.
    RAMADAN_QUIET = (time(16, 30), time(21, 0))

    # Step size used when scanning forward for the next allowed slot, and the
    # safety cap on how far ahead we will scan.
    _SCAN_STEP = timedelta(minutes=5)
    _SCAN_LIMIT = timedelta(days=8)

    def __init__(self, env=None):
        self.env = env

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_send_allowed(self, now, partner_tz=None):
        """Return ``(allowed: bool, reason: str)`` for the moment *now* as seen
        in the recipient's local timezone.

        :param now: datetime; naive is treated as UTC. None → current UTC time.
        :param partner_tz: IANA tz name of the recipient; falls back to the
            business default (Asia/Riyadh) when missing/invalid.
        """
        local = self._to_local(now, partner_tz)
        t = local.time()

        # Most specific religious reasons first, then the general business window.
        prayer = self._prayer_block(t)
        if prayer:
            return False, "prayer time (%s)" % prayer

        if local.weekday() == 4 and self._in_window(t, *self.FRIDAY_JUMUAH):
            return False, "Friday prayer (Jumu'ah)"

        if self._is_ramadan(local) and self._in_window(t, *self.RAMADAN_QUIET):
            return False, "Ramadan quiet hours"

        if not self._in_window(t, self.BUSINESS_START, self.BUSINESS_END):
            return False, "outside business hours"

        return True, "allowed"

    def next_allowed_slot(self, now, partner_tz=None):
        """Return the first allowed moment at/after *now* as a naive UTC
        datetime (seconds zeroed). Scans forward in small steps up to a safety
        cap; returns that cap if nothing is found (degenerate config)."""
        start = self._to_utc_aware(now).replace(second=0, microsecond=0)
        candidate = start
        deadline = start + self._SCAN_LIMIT
        while candidate <= deadline:
            allowed, _reason = self.is_send_allowed(candidate, partner_tz)
            if allowed:
                return candidate.astimezone(pytz.utc).replace(tzinfo=None)
            candidate += self._SCAN_STEP
        _logger.warning(
            "send_window: no allowed slot within %s of %s (check config)",
            self._SCAN_LIMIT, start,
        )
        return deadline.astimezone(pytz.utc).replace(tzinfo=None)

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
        try:
            tz = pytz.timezone(partner_tz) if partner_tz else pytz.timezone(self.DEFAULT_TZ)
        except Exception:
            tz = pytz.timezone(self.DEFAULT_TZ)
        return self._to_utc_aware(now).astimezone(tz)

    @staticmethod
    def _in_window(t, start, end):
        """True if start <= t < end (windows never cross midnight here)."""
        return start <= t < end

    def _prayer_block(self, t):
        """Return the name of the prayer whose block contains *t*, else None."""
        for name, ptime in self.PRAYER_TIMES.items():
            start_min = ptime.hour * 60 + ptime.minute
            end_min = start_min + self.PRAYER_BLOCK_MINUTES
            tmin = t.hour * 60 + t.minute
            if start_min <= tmin < end_min:
                return name
        return None

    @staticmethod
    def _is_ramadan(local_dt):
        if not _HIJRI_OK:
            return False
        try:
            hijri = _Gregorian(local_dt.year, local_dt.month, local_dt.day).to_hijri()
            return hijri.month == 9  # Ramadan is the 9th Hijri month
        except Exception:  # pragma: no cover
            return False

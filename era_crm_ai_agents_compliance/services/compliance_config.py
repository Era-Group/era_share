# -*- coding: utf-8 -*-
"""Single source of truth for all compliance policy settings.

Per the project "No Hardcoded Policy" rule, every send-window / norms / consent
value is a manager setting in ``ir.config_parameter`` under the
``era_crm_ai_agents_compliance.*`` namespace. This module:

  * holds the DEFAULTS (one place — these preserve the original KSA behavior, so
    an untouched install behaves exactly as before), and
  * exposes ``ComplianceConfig(env)`` which loads the namespace once and parses
    each value (bool / int / time / csv).

``ComplianceConfig`` performs the **approved elevation #7**: a read-only sudo
read of ONLY this namespace (``ir.config_parameter`` is system-readable only, so
a salesperson-scoped engine needs sudo to read its settings). It writes nothing
and reads no other namespace or secret.

If constructed without an env (pure unit tests), it falls back to DEFAULTS.
"""
from datetime import time

PREFIX = "era_crm_ai_agents_compliance."

# key (without prefix) -> default string. Defaults == original KSA behavior.
DEFAULTS = {
    # Send-window
    "send_window_enabled": "1",
    "working_hours_enabled": "1",
    "working_start": "09:00",
    "working_end": "21:00",
    "default_tz": "Asia/Riyadh",
    "weekend_enabled": "1",
    "weekend_days": "4,5",           # Mon=0 … Sun=6  → Fri,Sat (KSA)
    "prayer_enabled": "1",
    "prayer_block_minutes": "30",
    "jumuah_enabled": "1",
    "jumuah_start": "11:30",
    "jumuah_end": "13:30",
    "ramadan_enabled": "1",
    "ramadan_start": "16:30",
    "ramadan_end": "21:00",
    # Norms
    "norms_enabled": "1",
    "norms_check_greeting": "1",
    "norms_check_honorific": "1",
    "norms_check_tone": "1",
    # Consent / opt-out / DSAR
    "required_consent_type": "marketing",
    "opt_out_window_hours": "72",
    "dsar_erasure_mode": "anonymize",
    # Prayer source
    "prayer_source": "api",          # api | fixed
    "prayer_provider": "aladhan",
    "prayer_method": "4",            # Aladhan: 4 = Umm al-Qura (KSA)
    "default_city": "Riyadh",
    "default_country": "SA",
    # Fixed fallback times: Fajr,Dhuhr,Asr,Maghrib,Isha (local HH:MM)
    "prayer_fixed_times": "05:00,12:00,15:30,18:15,19:45",
}

_TRUE = {"1", "true", "yes", "on"}
_PRAYER_NAMES = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def _parse_time(value, fallback="00:00"):
    try:
        h, m = str(value).strip().split(":")
        return time(int(h), int(m))
    except Exception:
        h, m = fallback.split(":")
        return time(int(h), int(m))


class ComplianceConfig:
    def __init__(self, env=None):
        if env is not None:
            # Elevation #7: read-only sudo read of this namespace only.
            icp = env["ir.config_parameter"].sudo()
            self._raw = {k: icp.get_param(PREFIX + k, d) for k, d in DEFAULTS.items()}
        else:
            self._raw = dict(DEFAULTS)

    # -- typed accessors ------------------------------------------------
    def b(self, key):
        return str(self._raw.get(key, "")).strip().lower() in _TRUE

    def s(self, key):
        return (self._raw.get(key) or "").strip()

    def i(self, key, default=0):
        try:
            return int(self._raw.get(key))
        except Exception:
            return default

    def time(self, key):
        return _parse_time(self._raw.get(key, DEFAULTS.get(key, "00:00")))

    def weekend_days(self):
        out = set()
        for tok in self.s("weekend_days").split(","):
            tok = tok.strip()
            if tok.isdigit():
                out.add(int(tok))
        return out

    def fixed_prayer_times(self):
        """Return {name: time} from the configured fixed fallback CSV."""
        parts = [p.strip() for p in self.s("prayer_fixed_times").split(",")]
        result = {}
        for name, hhmm in zip(_PRAYER_NAMES, parts):
            result[name] = _parse_time(hhmm)
        return result

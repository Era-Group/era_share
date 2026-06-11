# -*- coding: utf-8 -*-
"""Cultural-norms engine — validates the discourse of an outbound message
against Saudi/Arabic business etiquette (Rule: cultural discourse norms).

Heuristic and pure Python — no LLM, no network, no DB, no sudo — so it is safe
to call from any salesperson-scoped agent and is fully unit-testable in
isolation. It is a *linting* layer: it flags likely etiquette problems so the
guard (1.5) and the human-approval layer can decide what to do; it never edits
the text and never sends.

Checks (Arabic and English / mixed text):
  - greeting   : an accepted greeting must be present (e.g. السلام عليكم،
                 حياكم الله, مرحبا, Dear); a purely informal opener (هلا, hey)
                 with no proper greeting is flagged as incorrect.
  - honorific  : an honorific must be present (شيخ / أستاذ / دكتور / Mr / Dr …).
  - tone       : shouting / excessive punctuation is flagged as harsh.

Arabic matching is done on a normalized form (diacritics + tatweel stripped,
alef/ya/ta-marbuta unified) so spelling variants still match.
"""
import re

from .compliance_config import ComplianceConfig

# --- Arabic normalization -------------------------------------------------
# Tashkeel (harakat) + superscript alef + tatweel.
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")


def _normalize(text):
    """Lowercase + strip Arabic diacritics/tatweel + unify common letter
    variants, so greeting/honorific lookups are spelling-tolerant."""
    s = text or ""
    s = _TASHKEEL.sub("", s)
    trans = {
        "أ": "ا", "إ": "ا", "آ": "ا",  # alef variants → bare alef
        "ى": "ي",                        # alef maqsura → ya
        "ة": "ه",                        # ta marbuta → ha
    }
    for src, dst in trans.items():
        s = s.replace(src, dst)
    return s.lower()


class CulturalNorms:
    """Stateless norms checker. ``env`` is accepted for forward compatibility
    (future configurable vocab) but is not required."""

    # Accepted greetings (Arabic + English). Stored raw; normalized at init.
    GREETINGS = [
        "السلام عليكم", "سلام عليكم", "وعليكم السلام",
        "حياكم الله", "حياك الله",
        "أهلا", "أهلا وسهلا", "مرحبا", "مرحبتين",
        "صباح الخير", "مساء الخير", "تحية طيبة",
        "hello", "hi ", "dear", "greetings",
        "good morning", "good afternoon", "good evening",
    ]
    # Informal/incorrect openers — if the message opens with one of these and
    # carries no accepted greeting, the greeting is flagged as incorrect.
    INFORMAL_OPENERS = ["هلا", "يا هلا", "هاي", "هلو", "yo", "hey", "sup"]

    # Accepted honorifics (Arabic + English).
    HONORIFICS = [
        "شيخ", "استاذ", "استاذه", "دكتور", "دكتوره", "د.", "مهندس", "م.",
        "سعاده", "معالي", "الاخ", "الاخت", "اخي", "اختي", "سيد", "سيده",
        "سيدي", "سيدتي", "ابو", "ام",
        "mr", "mrs", "ms", "dr", "eng", "sheikh", "sir", "madam", "ustaz",
    ]

    # Tone: a long run of '!' / '؟' or many shouting capitals reads as harsh.
    _SHOUT_PUNCT = re.compile(r"[!؟]{3,}")
    _SHOUT_CAPS = re.compile(r"\b[A-Z]{5,}\b")

    def __init__(self, env=None):
        self.env = env
        self.cfg = ComplianceConfig(env)
        greetings, informal, honorifics = self._load_vocab(env)
        self._greetings = [_normalize(g) for g in greetings]
        self._informal = [_normalize(g) for g in informal]
        self._honorifics = [_normalize(h) for h in honorifics]

    def _load_vocab(self, env):
        """Load the editable vocabulary from crm.ai.norm.term; fall back to the
        in-code defaults per category when the table is empty or there is no env
        (unit tests). Read runs as the caller — no sudo (users have read ACL)."""
        defaults = {
            "greeting": self.GREETINGS,
            "informal_opener": self.INFORMAL_OPENERS,
            "honorific": self.HONORIFICS,
        }
        if env is None or "crm.ai.norm.term" not in env:
            return defaults["greeting"], defaults["informal_opener"], defaults["honorific"]
        loaded = {"greeting": [], "informal_opener": [], "honorific": []}
        for term in env["crm.ai.norm.term"].search([("active", "=", True)]):
            loaded.setdefault(term.category, []).append(term.text)
        # Per-category fallback: if a category has no rows, use its defaults.
        for cat in loaded:
            if not loaded[cat]:
                loaded[cat] = defaults[cat]
        return loaded["greeting"], loaded["informal_opener"], loaded["honorific"]

    # ------------------------------------------------------------------
    def check_norms(self, text):
        """Return ``(ok: bool, issues: list[str])``. ``ok`` is False when any
        issue is found. Honors the master toggle and each per-check toggle —
        a toggle OFF skips that check entirely."""
        cfg = self.cfg
        if not cfg.b("norms_enabled"):
            return True, []

        issues = []
        raw = text or ""
        if not raw.strip():
            return False, ["empty message"]

        norm = _normalize(raw)

        # --- greeting --------------------------------------------------
        if cfg.b("norms_check_greeting"):
            has_greeting = any(g.strip() and g.strip() in norm for g in self._greetings)
            if not has_greeting:
                opener = norm.lstrip()[:24]
                if any(inf and opener.startswith(inf) for inf in self._informal):
                    issues.append("informal/incorrect greeting")
                else:
                    issues.append("missing greeting")

        # --- honorific -------------------------------------------------
        if cfg.b("norms_check_honorific") and not self._has_honorific(norm):
            issues.append("missing honorific")

        # --- tone ------------------------------------------------------
        if cfg.b("norms_check_tone") and \
                (self._SHOUT_PUNCT.search(raw) or self._SHOUT_CAPS.search(raw)):
            issues.append("harsh tone (shouting/excessive punctuation)")

        return (not issues), issues

    # ------------------------------------------------------------------
    def _has_honorific(self, norm):
        """Word-boundary-aware honorific lookup. Arabic has no case, and short
        tokens like 'mr'/'م.' must not match inside other words."""
        for h in self._honorifics:
            if not h:
                continue
            # Build a boundary pattern; honorifics may end with '.' (د. / م.).
            pat = r"(?<!\w)" + re.escape(h) + (r"" if h.endswith(".") else r"(?!\w)")
            if re.search(pat, norm):
                return True
        return False


# Module-level convenience matching the task's function signature.
_DEFAULT = CulturalNorms()


def check_norms(text):
    """Functional wrapper around :class:`CulturalNorms` using a shared default
    instance: ``ok, issues = check_norms(text)``."""
    return _DEFAULT.check_norms(text)

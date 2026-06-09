# -*- coding: utf-8 -*-
"""Record-driven PII redaction with re-insertion (PDPL).

The guard masks real personal data BEFORE it leaves the Kingdom and restores it
AFTER the model replies. Detection is RECORD-DRIVEN: we already know the real
values (pulled from the Odoo record in context — name, phone, mobile, email,
national ID), so we mask those exact values wherever they appear instead of
guessing linguistically. A regex net is a SECONDARY safety check for
phones/emails/IDs that are NOT tied to a known record field — and finding any
such value is a BLOCK condition, never a "mask and send anyway".

Arabic handling: a name part may appear in several forms / several times — every
occurrence of every known value (and each meaningful name part) is masked.

The mapping table lives only inside one :class:`Redactor` instance, for the
duration of one call. It is never persisted and never sent outbound.

Placeholder format: ``[[PII:KIND:N]]`` — plain ASCII so the model echoes it back
verbatim far more reliably than exotic unicode. On the way back we still defend
against a model that mangles or partially echoes a token (see :meth:`unmask`).
"""
import re

# A well-formed placeholder, e.g. [[PII:PERSON:1]]
_PLACEHOLDER_RE = re.compile(r"\[\[PII:[A-Z_]+:\d+\]\]")
# Residual / mangled fragments to detect a token that did NOT come back clean,
# e.g. "[[PII : PERSON" , "PII:PERSON:1" without brackets, stray "]]".
_RESIDUAL_RE = re.compile(r"\[\s*\[?\s*PII|PII\s*:\s*[A-Z_]+|PERSON:\d|PHONE:\d|EMAIL:\d")

# Secondary-net detectors for PII not tied to a record field (BLOCK triggers).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Saudi national / iqama id: 10 digits starting 1 or 2.
_NATIONAL_ID_RE = re.compile(r"\b[12]\d{9}\b")
# Phone: +966.., 00966.., 05.., or a bare 9+ digit run (with separators).
_PHONE_RE = re.compile(r"(?:\+?966|00966|0)?[\s\-]?5\d(?:[\s\-]?\d){7}")

# Name parts shorter than this are not masked on their own (too generic, would
# shred unrelated Arabic text). Full values are always masked regardless.
_MIN_NAME_PART_LEN = 3


class RedactionError(Exception):
    """Raised when re-insertion cannot complete cleanly (mangled placeholder).

    The guard catches this, audits it, and refuses to surface the response — it
    never leaks a raw token and never guesses the original value.
    """


def _phone_digits(value):
    """Reduce a phone string to its comparable digit core (drop +966 / leading 0)."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("966"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits.lstrip("0")
    return digits


class Redactor:
    """Builds a mask/unmask mapping from a set of known personal values.

    Usage::

        r = Redactor()
        r.add("PERSON", "محمد عبدالله القحطاني", split_parts=True)
        r.add("PHONE", "+966501234567")
        r.add("EMAIL", "m.qahtani@example.com")
        masked, leftover = r.mask(prompt)   # leftover = unmapped PII (BLOCK if any)
        restored = r.unmask(model_reply)    # raises RedactionError on mangled token
    """

    def __init__(self):
        # token -> real value (the in-memory mapping; never persisted/sent).
        self._map = {}
        # ordered list of (kind, value, token) used for masking (longest-first).
        self._entries = []
        # phone digit-cores -> token, for format-tolerant phone matching.
        self._phone_cores = {}
        self._counters = {}

    # ------------------------------------------------------------------
    # Building the map
    # ------------------------------------------------------------------
    def add(self, kind, value, split_parts=False):
        """Register a real personal value to be masked.

        :param kind: token category, e.g. 'PERSON', 'PHONE', 'EMAIL', 'NATIONAL_ID'.
        :param value: the real value as stored on the record.
        :param split_parts: for names — also register each meaningful word so a
            family name appearing alone is masked too (Arabic multi-form).
        """
        value = (value or "").strip()
        if not value:
            return
        self._register(kind, value)
        if kind == "PHONE":
            core = _phone_digits(value)
            if core:
                self._phone_cores[core] = self._token_for(kind, value)
        if split_parts:
            for part in value.split():
                part = part.strip()
                if len(part) >= _MIN_NAME_PART_LEN and part != value:
                    self._register(kind, part)

    def _register(self, kind, value):
        if value in [v for _, v, _ in self._entries]:
            return
        token = self._token_for(kind, value)
        self._map[token] = value
        self._entries.append((kind, value, token))

    def _token_for(self, kind, value):
        # Reuse a token if this exact value was already registered.
        for _k, v, t in self._entries:
            if v == value:
                return t
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return "[[PII:%s:%d]]" % (kind, self._counters[kind])

    @property
    def mapping(self):
        """The in-memory token->value map (for audit *counts* only, never logged raw)."""
        return dict(self._map)

    def has_values(self):
        return bool(self._entries)

    # ------------------------------------------------------------------
    # Masking (outbound)
    # ------------------------------------------------------------------
    def mask(self, text):
        """Replace every known value (all occurrences) with its placeholder.

        Longest values first, so a full name is masked before its sub-parts and
        we never double-mask an overlap. Phone formats are normalized so local
        (05..) and international (+966..) forms both map to the same token.

        :returns: ``(masked_text, leftover_pii)`` where ``leftover_pii`` is a list
            of (kind, snippet) found by the secondary regex net but NOT tied to a
            known value — the guard treats a non-empty list as a hard BLOCK.
        """
        if not text:
            return text, []
        masked = text
        # 1. Literal masking, longest-first.
        for _kind, value, token in sorted(
            self._entries, key=lambda e: len(e[1]), reverse=True
        ):
            if value in masked:
                masked = masked.replace(value, token)
        # 2. Phone format variants -> same token.
        def _phone_sub(match):
            core = _phone_digits(match.group(0))
            return self._phone_cores.get(core, match.group(0))
        masked = _PHONE_RE.sub(_phone_sub, masked)
        # 3. Secondary net: any PII pattern still present and unmasked => leftover.
        leftover = self._scan_leftover(masked)
        return masked, leftover

    def _scan_leftover(self, masked):
        """Find PII the record-driven pass did not account for (BLOCK triggers)."""
        leftover = []
        for kind, rx in (("EMAIL", _EMAIL_RE), ("NATIONAL_ID", _NATIONAL_ID_RE),
                         ("PHONE", _PHONE_RE)):
            for m in rx.finditer(masked):
                snippet = m.group(0)
                # Ignore anything sitting inside an already-placed placeholder.
                if "[[PII:" in masked[max(0, m.start() - 7):m.start()]:
                    continue
                leftover.append((kind, snippet))
        return leftover

    # ------------------------------------------------------------------
    # Re-insertion (inbound)
    # ------------------------------------------------------------------
    def unmask(self, text, strict=True):
        """Restore real values into the model's response.

        Substitutes every well-formed placeholder we issued back to its real
        value.

        :param strict: when True (OUR agents' path), defend against a model that
            mangled or partially echoed a token — if any unknown/residual token
            remains, raise :class:`RedactionError` rather than leak a raw token or
            guess (the guard then withholds the response). When False (native
            Ask-AI / ai_fields path), do the best-effort substitution and never
            raise — a leftover mangled fragment is a placeholder string, not real
            PII, so it is safe to return rather than break a native feature.
        """
        if not text:
            return text
        restored = _PLACEHOLDER_RE.sub(
            lambda m: self._map.get(m.group(0), m.group(0)), text
        )
        if strict:
            # Any well-formed token we don't recognise, or any residual fragment
            # of a token, means re-insertion is not clean -> refuse.
            unknown = [t for t in _PLACEHOLDER_RE.findall(restored) if t not in self._map]
            if unknown or _RESIDUAL_RE.search(restored):
                raise RedactionError(
                    "Re-insertion failed: the model returned a mangled or unknown "
                    "PII placeholder; response withheld."
                )
        return restored

# -*- coding: utf-8 -*-
"""Matching a plain-language question against a tour.

Kept deliberately simple and inspectable: an administrator tuning a tour's
keywords has to be able to predict what will match, and the request form shows
exactly which words were matched. Nothing here is Arabic- or English-specific
beyond normalisation, so a tour written in either language is matchable by a
question asked in the same one.
"""

import re
import unicodedata

# Harakat, tatweel and the Quranic annotation range. Users type with and
# without them interchangeably, so they must not affect matching.
_ARABIC_MARKS = re.compile(
    "[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭـ]"
)

# Letters Arabic typists use interchangeably. Folding them costs nothing here
# and saves the administrator from writing every spelling as a keyword.
_LETTER_FOLDING = str.maketrans({
    "أ": "ا",  # أ
    "إ": "ا",  # إ
    "آ": "ا",  # آ
    "ٱ": "ا",  # ٱ
    "ى": "ي",  # ى
    "ة": "ه",  # ة
    "ؤ": "و",  # ؤ
    "ئ": "ي",  # ئ
    "ک": "ك",  # ک  (Persian kaf)
    "ی": "ي",  # ی  (Persian yeh)
})

# Function words only. Verbs are never stripped — "create", "print", "أنشئ",
# "اطبع" carry most of the meaning of a how-do-I question.
_STOPWORDS = {
    # Arabic
    "كيف", "كيفيه", "وش", "ايش", "شلون", "طريقه", "الطريقه", "ممكن", "ابغي",
    "ابي", "اريد", "بغيت", "لو", "سمحت", "من", "في", "علي", "الي", "عن", "مع",
    "هل", "ما", "ماهي", "هذا", "هذه", "هذي", "ذلك", "انا", "احد", "شي", "شيء",
    "و", "او", "ثم", "بس", "عشان", "حتي", "يكون", "تكون", "اقدر", "يقدر",
    # Asking where something is says nothing about what it is.
    "وين", "اين", "فين", "القي", "الاقي", "لقي", "اشوف", "شوف", "اجد", "اوصل",
    # Nor does asking to be shown it. These are the same group as the line
    # above and were missing from it, which cost every question phrased "show
    # me the products" a model call and eight seconds to reach a menu the
    # matcher had in front of it. Note "عرض" is absent on purpose: on its own
    # it is a quotation, which is a thing rather than a request to see one.
    "اظهر", "اظهرلي", "اعرض", "ورني", "وريني", "لي", "لنا",
    "show", "display", "view",
    # English
    "how", "what", "where", "which", "when", "why", "do", "does", "did", "i",
    "we", "you", "to", "a", "an", "the", "is", "are", "can", "could", "would",
    "should", "my", "me", "it", "this", "that", "for", "of", "in", "on", "and",
    "or", "please", "want", "need", "find", "locate", "see", "go", "reach",
}

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Suffixes worth stripping to a stem. Short ones are only removed from long
# enough tokens, otherwise "طلب" itself would start losing letters.
#
# The bare "ه" carries the feminine ending, which normalisation has already
# folded ة into. Without it "شركة" and "شركات" stem apart and a question about
# a company never reaches a menu called Companies — leaving it out is what
# made "how do I edit the company details" find nothing.
_SUFFIXES = ("ات", "ين", "ون", "ها", "هم", "يه", "ية", "ه")

# An Arabic letter, after normalisation has folded the hamza forms away.
_AR = r"[ء-ي]"

# Broken plurals are not the singular plus a suffix — they re-shape the word
# around the same consonants — so stripping suffixes can never reach them, and
# a menu called "عروض الأسعار" stays invisible to somebody asking about a
# "عرض سعر". These are the shapes an Odoo menu tree actually uses; each maps a
# plural back to the singular a user types.
#
# A pattern that fires on a word that was not a plural only adds a form that
# matches nothing, which costs a comparison. Adding a form can never hide a
# match that already worked.
_BROKEN_PLURALS = (
    # أفعال — أسعار → سعر, أصناف → صنف, أنواع → نوع, أقسام → قسم
    (re.compile("^ا(%s)(%s)ا(%s)$" % (_AR, _AR, _AR)), r"\1\2\3"),
    # فعول — عروض → عرض, بنود → بند, عقود → عقد, حقول → حقل
    (re.compile("^(%s)(%s)و(%s)$" % (_AR, _AR, _AR)), r"\1\2\3"),
    # فعلاء — عملاء → عميل, وكلاء → وكيل, شركاء → شريك, مدراء → مدير
    (re.compile("^(%s)(%s)(%s)اء$" % (_AR, _AR, _AR)), r"\1\2ي\3"),
    # فعائل — قوائم → قائمة, رسائل → رسالة (the ة is folded and then stripped
    # as a suffix, so both sides meet at the bare stem)
    (re.compile("^(%s)وا(%s)(%s)$" % (_AR, _AR, _AR)), r"\1ا\2\3"),
    # فواعيل — فواتير → فاتورة, قوائم بريد → …, مواعيد → موعد
    (re.compile("^(%s)وا(%s)ي(%s)$" % (_AR, _AR, _AR)), r"\1ا\2و\3"),
)


def normalize(text):
    """Fold a string to its comparable form."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _ARABIC_MARKS.sub("", text)
    text = text.translate(_LETTER_FOLDING)
    return text.casefold()


def _variants(token):
    """The token plus the shorter forms a user might have typed instead."""
    out = {token}
    bare = token[2:] if len(token) > 4 and token.startswith("ال") else token
    out.add(bare)
    for suffix in _SUFFIXES:
        if len(bare) > len(suffix) + 2 and bare.endswith(suffix):
            out.add(bare[: -len(suffix)])
    if len(bare) > 3 and bare.endswith("s"):
        out.add(bare[:-1])
    # A broken plural can sit under the article and under a suffix, so try
    # every form reached above rather than only the token as typed.
    for form in tuple(out):
        for pattern, singular in _BROKEN_PLURALS:
            if pattern.match(form):
                out.add(pattern.sub(singular, form))
    return out


def stem(token):
    """The most reduced form of a token.

    Two questions whose words stem to the same thing are the same question,
    which is what keeps "الطلب" and "طلب" on one demand counter instead of
    two. Ties break on the word itself so the result never depends on set
    iteration order.
    """
    return min(_variants(token), key=lambda variant: (len(variant), variant))


def tokenize(text):
    """Meaningful tokens of a string, each expanded to its variants."""
    tokens = set()
    for raw in _TOKEN_RE.findall(normalize(text)):
        if len(raw) < 2 or raw in _STOPWORDS:
            continue
        tokens |= _variants(raw)
    return tokens


def question_tokens(text):
    """Tokens of the question itself, without variant expansion.

    The score is a fraction of what the user actually asked, so the
    denominator has to count words, not the variants they expand to.
    """
    return {
        raw
        for raw in _TOKEN_RE.findall(normalize(text))
        if len(raw) >= 2 and raw not in _STOPWORDS
    }


def coverage(question, subject):
    """How much of ``subject`` the question named, from 0.0 to 1.0.

    The mirror image of :func:`score`, and the right question to ask of a
    menu. "Where do I find Discuss" only spends one of its three words on the
    menu's name, which scores badly the other way round — but it names the
    menu completely, which is what actually matters when picking one.
    Returns ``(coverage, matched_words)``.
    """
    wanted = question_tokens(subject)
    if not wanted:
        return 0.0, []
    asked = tokenize(question)
    matched = sorted(
        word for word in wanted if _variants(word) & asked
    )
    if not matched:
        return 0.0, []
    return len(matched) / len(wanted), matched


def balanced(question, subject, ignore=()):
    """Agreement between question and subject, from 0.0 to 1.0.

    Neither direction is trustworthy alone. Measuring the question only
    punishes a short menu name; measuring the subject only lets a single
    incidental word carry a whole match — "export payroll to the bank file"
    covers a menu called "Banks" completely, and walking someone there is
    worse than admitting nothing was found. The harmonic mean needs both to
    hold up. ``ignore`` drops words that state intent rather than target.

    Returns ``(agreement, matched_words)``.
    """
    ignored = {stem(word) for word in ignore}
    asked = {
        word for word in question_tokens(question) if stem(word) not in ignored
    }
    wanted = question_tokens(subject)
    if not asked or not wanted:
        return 0.0, []

    known = set()
    for word in wanted:
        known |= _variants(word)
    matched_asked = {word for word in asked if _variants(word) & known}
    if not matched_asked:
        return 0.0, []

    asked_variants = set()
    for word in asked:
        asked_variants |= _variants(word)
    matched_wanted = [word for word in wanted if _variants(word) & asked_variants]

    question_side = len(matched_asked) / len(asked)
    subject_side = len(matched_wanted) / len(wanted)
    agreement = (
        2 * question_side * subject_side / (question_side + subject_side)
    )
    return agreement, sorted(matched_asked)


def score(question, subject, keywords=""):
    """How well ``subject`` answers ``question``, from 0.0 to 1.0.

    ``keywords`` is scored the same way but earns a bonus, so an administrator
    can make a tour reachable by wording nobody wrote in its title.
    Returns ``(score, matched_words)``.
    """
    asked = question_tokens(question)
    if not asked:
        return 0.0, []

    # Both directions, for the reason menus need both. A generated tour is
    # described by the whole question somebody once asked, so a new short
    # question sharing one incidental word with that sentence covered half of
    # itself and passed: "كيف اعمل فاتورة" scored exactly 0.500 against the
    # walkthrough for "كيف اعمل منتج تصنيعي بثلاث مكونات خام", on the strength
    # of the verb عمل alone, and started a manufacturing walkthrough for
    # somebody asking about an invoice. Measured on the live database.
    agreement, matched = balanced(question, subject)

    keyword_tokens = tokenize(keywords)
    if keyword_tokens:
        asked_variants = {word: _variants(word) for word in asked}
        by_keyword = sorted(
            word for word, variants in asked_variants.items()
            if variants & keyword_tokens
        )
        if by_keyword:
            # An administrator filling in "Also Matches" is stating that this
            # wording should reach this tour. That is a decision about the
            # question, not a claim about the tour's own words, so it is
            # measured on the question alone and keeps the bonus that lets a
            # deliberate synonym beat an incidental overlap.
            keyed = min(len(by_keyword) / len(asked) + 0.15, 1.0)
            if keyed > agreement:
                return keyed, by_keyword

    return agreement, matched

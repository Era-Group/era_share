"""Turn the corpus's plain text into the shape a statute is read in.

The corpus is stored as text because that is what an embedding needs: one long
string, its markers flattened. Read by a person it is a wall — every article
prefixed with the name of the instrument it belongs to, every definition on a
line of its own with a dash in front of it, section titles wrapped in equals
signs. All of it is structure, only written for a machine.

So it is put back, deterministically. No model is asked; the same text always
comes out the same way. What is recognised:

    == التعريفات ==                     a section
    «نظام كذا» — المادة الأولى:: نصها    an article: the repeated name is dropped
    - المجلس: المجلس الأعلى للقضاء.      a definition — the term is picked out
    أ - النص الوارد ...                  a lettered branch, its marker kept
    ١- يتولى المجلس ...                  a numbered branch

Everything else is a paragraph. Nothing is concatenated: every value goes in
through Markup's %-formatting, which escapes it, so a statute that happens to
contain a tag is read as text rather than markup.
"""
import re

from markupsafe import Markup

# The line the sync writes at the top of every document: title, kind, status.
# It is what the citation card already shows, so it is dropped here.
HEADER = re.compile(r'^.+—\s*النوع:\s*.+—\s*الحالة:\s*.+$')
SECTION = re.compile(r'^==\s*(?P<title>.+?)\s*==$')
# «the instrument» — المادة الأولى:: the text. The name repeats on every
# article, which is noise to a reader who is already inside that instrument.
ARTICLE = re.compile(r'^«(?P<law>[^»]{1,300})»\s*[—–-]\s*(?P<label>[^:]{1,80}?)\s*:{1,2}\s*(?P<rest>.*)$')
BULLET = re.compile(r'^[-–•]\s*(?P<text>.+)$')
# أ - ... / ب) ... / ١- ... / 3. ... — an ordered branch whose marker carries
# meaning: the article refers to it as "الفقرة (ب)".
MARKER = re.compile(r'^(?P<marker>(?:[أ-ي]|[٠-٩0-9]{1,3}))\s*[-–.)]\s*(?P<text>.+)$')
# A definition reads "term: what it means", and only when the term is short.
TERM = re.compile(r'^(?P<term>[^:]{1,40}):\s*(?P<rest>.+)$')


def _line_token(line):
    """What one line is, and what it carries."""
    if match := SECTION.match(line):
        return ('section', match['title'])
    if match := ARTICLE.match(line):
        return ('article', (match['label'], match['rest'].strip()))
    if match := BULLET.match(line):
        return ('bullet', match['text'])
    if match := MARKER.match(line):
        return ('marker', (match['marker'], match['text']))
    return ('paragraph', line)


def _bullet_item(text):
    """A definition shows its term; anything else is a plain item."""
    match = TERM.match(text)
    if not match:
        return Markup('<li>%s</li>') % text
    return Markup('<li><b>%s:</b> %s</li>') % (match['term'], match['rest'])


def format_legal_text(text):
    """Plain corpus text in, readable HTML out. No model, no network.

    :param str text: the stored text of one statute
    :return: markup safe to render, empty if there was nothing to format
    :rtype: markupsafe.Markup
    """
    if not text or not text.strip():
        return Markup('')

    tokens = []
    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if not line or HEADER.match(line):
            continue
        tokens.append(_line_token(line))

    parts = []
    index = 0
    while index < len(tokens):
        kind, payload = tokens[index]
        if kind in ('bullet', 'marker'):
            # Consecutive items of one kind are one list, however many blank
            # lines the source put between them.
            items = []
            while index < len(tokens) and tokens[index][0] == kind:
                value = tokens[index][1]
                items.append(_bullet_item(value) if kind == 'bullet'
                             else Markup('<li><b>%s -</b> %s</li>') % (value[0], value[1]))
                index += 1
            css = 'mb-3' if kind == 'bullet' else 'list-unstyled ps-3 mb-3'
            parts.append(Markup('<ul class="%s">%s</ul>') % (css, Markup('').join(items)))
            continue
        if kind == 'section':
            parts.append(Markup('<h5 class="mt-4 mb-2 fw-bold">%s</h5>') % payload)
        elif kind == 'article':
            label, rest = payload
            parts.append(Markup('<h6 class="mt-3 mb-1 fw-bold text-muted">%s</h6>') % label)
            if rest:
                # An article can open straight into its first branch.
                sub_kind, sub_payload = _line_token(rest)
                if sub_kind == 'marker':
                    parts.append(Markup('<ul class="list-unstyled ps-3 mb-3"><li><b>%s -</b> %s</li></ul>')
                                 % (sub_payload[0], sub_payload[1]))
                else:
                    parts.append(Markup('<p class="mb-2">%s</p>') % rest)
        else:
            parts.append(Markup('<p class="mb-2">%s</p>') % payload)
        index += 1

    return Markup('<div class="o_era_legal_text">%s</div>') % Markup('').join(parts)

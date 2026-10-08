"""Pull the hymn lyrics out of the Order of Worship text (as pypdf extracts it).

The OW prints each congregational hymn as a header line, the verses, then the next section:

  *Hymn of Gathering                 Creator God You Gave Us Life                OLD HUNDRETH
  Creator God, you gave us life,
  ...
  (blank line between verses)
  ...
  -lyrics by Ruth C. Duck
  *Prayer of Awareness                       Rev. Fregin

update_service_titles.py writes them to service-titles/<key>-lyrics.txt (verses separated by a
blank line) so they reach the Drive folder with the other title files; the ProPresenter Mac's
sync_propresenter_hymns.py builds the hymn decks from them.
"""
import re

HYMNS = {
    # "*Hymn for the Journey", also "*Easter Hymn for the Journey"
    "hymn-of-gathering": re.compile(r"^\*?\s*(?:\w+\s+)?Hymn of Gathering\b", re.I),
    "hymn-for-journey": re.compile(r"^\*?\s*(?:\w+\s+)?Hymn for the Journey\b", re.I),
}

# What ends a hymn: the next section's header ("*Prayer of Awareness      Rev. Fregin",
# "Postlude            Dr. Christoph Bull"), or a credit ("-lyrics by Ruth Duck", "[Lyrics by ...]").
COLUMN_GAP = re.compile(r"\S {4,}\S")
CREDIT = re.compile(r"^\s*([-–—]+\s*\S|\[.*\]\s*$|(words|lyrics|music|text)( and music)?\s+(by|adapted)\b)",
                    re.I)
HEADING = re.compile(r"^\s*(\*|postlude\b|benediction\b|inside the music\b|announcements\b|"
                     r"please (be seated|stand|remain)\b)", re.I)
# Not lyrics, though printed with them: copyright/performer notes and singing instructions.
NOT_LYRICS = re.compile(r"©|copyright|\binc\.|,\s*conductor\s*$|used by permission|onelicense|\bccli\b|"
                        r"\bwe will sing\b|\bin response to the leader\b", re.I)
REFRAIN = re.compile(r"^(refrain|chorus)\b\s*:?\s*", re.I)
TRAILING_NOTE = re.compile(r"\s*(\[[^\]]*\]|\((repeat|refrain|chorus)[^)]*\))\s*$", re.I)


def is_end(line):
    s = line.strip()
    return bool(COLUMN_GAP.search(s) or CREDIT.match(s) or HEADING.match(s))


def verses_after(lines):
    """Verses from the lines following a hymn header, with refrains written out each time."""
    verses, verse, refrain = [], [], None
    is_refrain = False

    def close():
        nonlocal verse, refrain, is_refrain
        if verse:
            verses.append(verse)
            if is_refrain:
                refrain = verse
        verse, is_refrain = [], False

    for line in lines:
        if is_end(line):
            break
        s = TRAILING_NOTE.sub("", re.sub(r"\s+", " ", line).strip())
        if not s:
            close()
            continue
        if NOT_LYRICS.search(s):
            continue
        m = REFRAIN.match(s)
        if m:
            rest = s[m.end():]
            if rest:                         # "Refrain: Leaning, leaning," starts the refrain
                close()
                is_refrain = True
                verse.append(rest)
            else:                            # a bare "Refrain" means sing it again here
                close()
                if refrain:
                    verses.append(list(refrain))
            continue
        verse.append(s)
    close()
    return verses


def extract(text):
    """{"hymn-of-gathering": [[line, ...], ...verses], "hymn-for-journey": [...]}; a hymn the
    OW doesn't print is left out."""
    lines = text.splitlines()
    out = {}
    for key, header in HYMNS.items():
        start = next((i for i, l in enumerate(lines) if header.match(l)), None)
        if start is None:
            continue
        verses = verses_after(lines[start + 1:])
        if verses:
            out[key] = verses
    return out


def to_file_text(verses):
    return "\n\n".join("\n".join(v) for v in verses) + "\n"


def from_file_text(text):
    return [v.splitlines() for v in re.split(r"\n\s*\n", text.strip()) if v.strip()]

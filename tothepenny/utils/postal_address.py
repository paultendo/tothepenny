# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The account holder's postal address, from the block printed under their name.

UK statements print the holder's name and address as a block for a window envelope. The address is read by position
from the positioned words of the first page: the lines directly below the name that start at the name's left edge,
down to and including a UK postcode, or for an address abroad a line that is a country's name. Text beside the block (a statement summary on the same lines) is left out, and a
line of another block between two address lines is skipped. Without a postcode within a few lines the block is not an
address and nothing is returned: the reader reports only what it can show.

A file may hold several statements (a year of them merged into one PDF), so every page is read: each distinct address
is reported with the pages it appears on, and a change of address partway through is kept, never collapsed into the
first page's.
"""
import re
from typing import Iterable, List, Optional

from .countries import COUNTRIES

POSTCODE = re.compile(r"^(?:GIR ?0AA|[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2})$", re.IGNORECASE)
MAX_LINES = 6        # address lines under the name
EDGE = 3.0           # points: how far a line may start from the name's left edge
WORD_GAP = 14.0      # points: a wider gap ends the phrase (the next text belongs to another block)
REACH = 90.0         # points below the name within which the block must end


def _lines(words: Iterable[dict], tolerance: float = 2.0) -> List[List[dict]]:
    rows: List[List[dict]] = []
    for word in sorted((w for w in words if (w.get("text") or "").strip()), key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - word["top"]) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w["x0"]) for row in rows]


def _phrase_from(row: List[dict], start: int) -> str:
    parts = [row[start]["text"]]
    for prev, word in zip(row[start:], row[start + 1:]):
        if word["x0"] - prev["x1"] > WORD_GAP:
            break
        parts.append(word["text"])
    return " ".join(parts).strip()


TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "sir", "lady", "rev"}


def _name_key(tokens: List[str]) -> Optional[tuple]:
    """(first forename, surname) without title, middle names or initials, and '&': the holder's name matches the
    name above the address however the statement prints it ("Mrs Yaffa Jones" and "Ms Yaffa Jones"; "Michelle Mary
    Butler" and "Michelle M Butler")."""
    words = [t.strip(".,").lower() for t in tokens if t.strip(".,&")]
    words = [w for w in words if w not in TITLES]
    return (words[0], words[-1]) if len(words) >= 2 else None


def _same_person(a: Optional[tuple], b: Optional[tuple]) -> bool:
    """Same surname, and the same forename or one printed as its initial ("Y Jones" and "Yaffa Jones")."""
    if not a or not b or a[1] != b[1]:
        return False
    x, y = a[0], b[0]
    return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))


def _name_start(row: List[dict], key: tuple) -> Optional[int]:
    """Index of the word where the holder's name starts on this line, when the line's leading phrase is that name."""
    for start in range(len(row)):
        phrase = []
        for word in row[start:]:
            if phrase and word["x0"] - phrase[-1]["x1"] > WORD_GAP:
                break
            phrase.append(word)
        if _same_person(_name_key([w["text"] for w in phrase]), key):
            return start
    return None


def address_from_words(words: Iterable[dict], holder: Optional[str]) -> Optional[str]:
    """The address under the holder's name on a page, joined with commas; None when there is no such block."""
    key = _name_key(holder.split()) if holder else None
    if not key:
        return None
    rows = _lines(words)
    for index, row in enumerate(rows):
        start = _name_start(row, key)
        if start is not None:
            left, top = row[start]["x0"], row[start]["top"]
            parts: List[str] = []
            for below in rows[index + 1:]:
                if below[0]["top"] - top > REACH or len(parts) >= MAX_LINES:
                    break
                first = next((i for i, w in enumerate(below) if abs(w["x0"] - left) <= EDGE), None)
                if first is None:
                    continue  # another block's line between two address lines
                phrase = _phrase_from(below, first)
                if not phrase or "£" in phrase:
                    break
                parts.append(phrase)
                if POSTCODE.match(phrase) or (len(parts) >= 3 and phrase.strip(" .,").upper() in COUNTRIES):
                    return ", ".join(parts)
            # The name's appearance with no postcode under it: try its next appearance.
    return None


def addresses_by_page(pages, holder: Optional[str]) -> List[dict]:
    """Every distinct address under the holder's name across the pages, in order of first appearance, each with the
    pages it appears on: [{"address": ..., "pages": [1, 2, ...]}]."""
    found: List[dict] = []
    for number, page in enumerate(pages, 1):
        # A page object or a plain mapping; a page with no words (a scan, a blank page) simply has no address.
        words = page.words if hasattr(page, "words") else page.get("words", [])
        address = address_from_words(words or [], holder)
        if not address:
            continue
        entry = next((e for e in found if e["address"] == address), None)
        if entry is None:
            found.append({"address": address, "pages": [number]})
        else:
            entry["pages"].append(number)
    return found

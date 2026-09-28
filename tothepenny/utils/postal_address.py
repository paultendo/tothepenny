# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""The account holder's postal address, from the block printed under their name.

UK statements print the holder's name and address as a block for a window envelope. The address is read by position
from the positioned words of the first page: the lines directly below the name that start at the name's left edge,
down to and including a UK postcode. Text beside the block (a statement summary on the same lines) is left out, and a
line of another block between two address lines is skipped. Without a postcode within a few lines the block is not an
address and nothing is returned: the reader reports only what it can show.
"""
import re
from typing import Iterable, List, Optional

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


def address_from_words(words: Iterable[dict], holder: Optional[str]) -> Optional[str]:
    """The address under the holder's name on a page, joined with commas; None when there is no such block."""
    if not holder:
        return None
    name = [t.lower() for t in holder.split()]
    rows = _lines(words)
    for index, row in enumerate(rows):
        tokens = [w["text"].lower() for w in row]
        for start in range(len(tokens) - len(name) + 1):
            if tokens[start:start + len(name)] != name:
                continue
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
                if POSTCODE.match(phrase):
                    return ", ".join(parts)
            break  # the name's first appearance with no postcode under it: try the next appearance
    return None

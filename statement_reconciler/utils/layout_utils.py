# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Utilities for working with OCR/Vision word layouts.

These helpers intentionally avoid heavy dependencies (e.g., OpenCV/numpy) so
they can be used by cloud-based extractors and local OCR alike.
"""

from __future__ import annotations

from typing import Optional, Tuple


def normalize_word_layout_to_width(
    *,
    words: list[dict],
    source_width: float,
    source_height: float,
    target_width: float = 600.0,
) -> Tuple[list[dict], float, float]:
    """
    Normalize word coordinates to a pdfplumber-like coordinate space.

    Many existing parsers assume PDF points with a page width around ~600.
    This rescales coordinates so downstream layout parsers can re-use their heuristics.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValueError("source_width/source_height must be positive")

    scale = float(target_width) / float(source_width)
    target_height_scaled = float(source_height) * scale

    normalized: list[dict] = []
    for word in words:
        text = (word.get("text") or "").strip()
        if not text:
            continue
        x0 = float(word.get("x0", 0.0)) * scale
        x1 = float(word.get("x1", 0.0)) * scale
        top = float(word.get("top", 0.0)) * scale
        bottom = float(word.get("bottom", 0.0)) * scale
        normalized.append(
            {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
                "conf": word.get("conf"),
            }
        )

    return normalized, float(target_width), float(target_height_scaled)


def reconstruct_text_from_words(
    words: list[dict],
    *,
    page_width: float,
    y_tolerance: float = 1.8,
    target_columns: int = 220,
) -> str:
    """
    Approximate pdftotext -layout by projecting words onto a fixed-width grid.

    This is useful for feeding downstream, text-based parsers while keeping columns
    reasonably aligned.
    """
    if not words:
        return ""

    scale = max(float(page_width) / float(target_columns), 2.0)
    sorted_words = sorted(words, key=lambda w: (w.get("top", 0.0), w.get("x0", 0.0)))

    lines: list[str] = []
    current_row: list[dict] = []
    current_top: Optional[float] = None

    def flush_row(row_words: list[dict]) -> None:
        if not row_words:
            return
        line_chars = [" "] * (target_columns + 20)

        for word in row_words:
            token = (word.get("text") or "").strip()
            if not token:
                continue
            x0 = float(word.get("x0", 0.0))
            start_idx = int(x0 / scale)
            start_idx = max(0, min(start_idx, len(line_chars) - 1))

            for offset, ch in enumerate(token):
                pos = start_idx + offset
                if pos >= len(line_chars):
                    break
                line_chars[pos] = ch

            next_pos = start_idx + len(token)
            if next_pos < len(line_chars):
                line_chars[next_pos] = " "

        line_text = "".join(line_chars).rstrip()
        if line_text.strip():
            lines.append(line_text)

    for word in sorted_words:
        top = float(word.get("top", 0.0))
        if current_row and current_top is not None and abs(top - current_top) > y_tolerance:
            flush_row(current_row)
            current_row = [word]
            current_top = top
        else:
            if not current_row:
                current_top = top
            current_row.append(word)

    flush_row(current_row)
    return "\n".join(lines)


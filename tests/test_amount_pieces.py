# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Amounts some text layers draw in pieces (Nationwide's older FlexAccount statements) are read as one amount."""
from tothepenny.extractors.page_reader import _join_decimals


def _piece(text, x0, x1, top=100.0):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 0.07, "size": 1.0}


def _texts(words):
    return [w["text"] for w in _join_decimals(words)]


def test_pieces_of_one_amount_are_joined():
    assert _texts([_piece("14.", 392.0, 403.4), _piece("71", 403.0, 411.0)]) == ["14.71"]
    assert _texts([_piece("18", 385.0, 394.34), _piece("7.63", 393.84, 410.8)]) == ["187.63"]
    assert _texts([_piece("9.", 284.69, 292.25), _piece("5", 291.6, 296.86), _piece("3", 296.35, 301.39)]) == ["9.53"]
    assert _texts([_piece("28.4", 280.08, 297.79), _piece("7", 297.29, 301.9)]) == ["28.47"]


def test_pieces_a_fraction_of_a_point_apart_vertically_still_join():
    assert _texts([_piece("52.", 392.0, 404.0, top=340.49), _piece("11", 403.0, 411.0, top=340.51)]) == ["52.11"]


def test_separate_numbers_stay_apart():
    assert _texts([_piece("070246", 100.0, 130.0), _piece("47780740", 133.0, 170.0)]) == ["070246", "47780740"]
    assert _texts([_piece("12.50", 270.0, 297.0), _piece("3.00", 296.8, 320.0)]) == ["12.50", "3.00"]
    assert _texts([_piece("2023", 48.0, 67.0), _piece("40.00", 67.1, 90.0)]) == ["2023", "40.00"]

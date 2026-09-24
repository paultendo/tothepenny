# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Regression tests for Barclays account-holder extraction.

Covers a header layout where an FSCS disclaimer block follows the
"Barclays Bank Account statement" NOTICEBOARD heading. An account-holder
pattern anchored on that heading grabbed the third following line, which
is the FSCS disclaimer, so the holder came back as
"Services Compensation Scheme". All names and addresses are synthetic.
"""
from __future__ import annotations

import re

import pytest

from tothepenny.config import get_bank_config_loader


@pytest.fixture(scope="module")
def account_name_pattern():
    loader = get_bank_config_loader()
    config = loader.get_config("barclays")
    if config is None:
        pytest.skip("Barclays configuration not available")
    pattern = config.header_patterns.get("account_name")
    if not pattern:
        pytest.skip("Barclays account_name pattern not configured")
    return re.compile(pattern, re.MULTILINE)


def _first_holder(pattern, text):
    """Replicate the pipeline's first-match header extraction."""
    match = pattern.search(text)
    if not match:
        return None
    if match.groups():
        for group in match.groups():
            if group is not None:
                return group.strip()
    return match.group(0).strip()


# Synthetic Barclays header in the NOTICEBOARD layout. The FSCS disclaimer
# block sits after the NOTICEBOARD heading and would previously be captured
# as the holder.
NOTICEBOARD_LAYOUT_HEADER = """\
Statement date 27 Oct 2025 Barclays Bank
Last statement 25 Apr 2025
Account
26 Apr - 27 Oct 2025
Ms Jane Mary Ann Example
• Sort Code 00-00-00
• Account no. 12345678
MS JANE MARY ANN EXAMPLE
1 SAMPLE STREET At a glance
SAMPLETOWN
CITY Start balance £0.00
NOTICEBOARD
Your Barclays Bank Account statement
Your deposit is eligible for
protection by the Financial
Services Compensation Scheme.
Current account statement
Your transactions
"""

# Synthetic Barclays header in the address-block layout (holder name above
# the statement date, postal address below).
ADDRESS_BLOCK_LAYOUT_HEADER = """\
Your statement
Mr Anthony Neil Another
27 Feb 2025
MR A N ANOTHER 
12 SAMPLE APARTMENTS
EXAMPLE CRESCENT
ANYTOWN
SAMPLESHIRE
AB12 3CD
Your accounts at a glance
"""


def test_holder_is_real_name_not_fscs_boilerplate(account_name_pattern):
    holder = _first_holder(account_name_pattern, NOTICEBOARD_LAYOUT_HEADER)
    assert holder == "Ms Jane Mary Ann Example"
    # The defining regression: must never be the FSCS boilerplate.
    assert "Compensation Scheme" not in (holder or "")


def test_holder_extraction_handles_alternate_layout(account_name_pattern):
    holder = _first_holder(account_name_pattern, ADDRESS_BLOCK_LAYOUT_HEADER)
    assert holder == "Mr Anthony Neil Another"
    assert "Compensation Scheme" not in (holder or "")


def test_pattern_does_not_match_fscs_lines_directly(account_name_pattern):
    # FSCS boilerplate has no title prefix and must not match at all.
    boilerplate = "protection by the Financial\nServices Compensation Scheme.\n"
    assert account_name_pattern.search(boilerplate) is None

# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Every account a file's statements are for, page by page.

A file may hold several statements merged together, and they need not all be for one account: a year of statements
for a sole account and a joint one, say. The bank's own header patterns (account number, sort code, holder) are applied
to each page, and each distinct account is reported with the pages it appears on, so a second account is never hidden
behind the first one found.
"""
import re
from typing import Dict, List, Optional


def _first(pattern: Optional[str], text: str) -> Optional[str]:
    if not pattern:
        return None
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return None
    # A pattern of alternatives captures in whichever group matched; a pattern with no group is the whole match.
    value = next((g for g in match.groups() if g), None) if match.groups() else match.group(0)
    return re.sub(r"\s+", " ", value).strip() if value else None


def accounts_by_page(pages, header_patterns: Dict[str, str]) -> List[dict]:
    """[{"sort_code", "account_number", "holder", "pages"}] in order of first appearance; a page without an account
    number of its own belongs to no account here (continuation pages usually print none)."""
    found: List[dict] = []
    for index, page in enumerate(pages, 1):
        number = getattr(page, 'number', None) or index
        try:
            text = page.layout() if hasattr(page, "layout") else page.get("text", "")
        except Exception:  # noqa: BLE001
            continue
        account = _first(header_patterns.get("account_number"), text or "")
        if not account:
            continue
        digits = re.sub(r"\D", "", account)
        sort_code = _first(header_patterns.get("sort_code"), text)
        sort_digits = re.sub(r"\D", "", sort_code or "")
        entry = next((e for e in found if re.sub(r"\D", "", e["account_number"]) == digits
                      and (not sort_digits or not e["sort_code"] or re.sub(r"\D", "", e["sort_code"]) == sort_digits)), None)
        if entry is None:
            found.append({"sort_code": sort_code, "account_number": account,
                          "holder": _first(header_patterns.get("account_name"), text), "pages": [number]})
        else:
            entry["pages"].append(number)
            entry["sort_code"] = entry["sort_code"] or sort_code
            entry["holder"] = entry["holder"] or _first(header_patterns.get("account_name"), text)
    return found

# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. statement-reconciler by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Native Revolut account statements, including Pockets and linked accounts.

Columns come from each table's printed headers, sections from their titles,
and control totals from the balance summary. No customer, date, amount or
page position is configured outside the source document. Unsupported shapes
fail rather than being silently flattened into one account.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..extractors.page_reader import read_pages

from ..models import ExtractionResult, Statement, Transaction
from ..validators.balance_validator import BalanceValidator


@dataclass(frozen=True)
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class RevolutParser:
    DATE = re.compile(r"^\d{1,2} [A-Za-z]+ \d{4}$")
    MONEY = re.compile(r"^-?£[\d,]+\.\d{2}$")
    SECTION = re.compile(r"^(.+?) transactions from (\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})$")
    REVERTED = re.compile(r"^Reverted from (\d{1,2} [A-Za-z]+ \d{4}) to (\d{1,2} [A-Za-z]+ \d{4})$")

    @staticmethod
    def _date(value):
        value = re.sub(r"\bSept\b", "Sep", value)
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        raise ValueError(f"Unsupported Revolut date: {value}")

    @staticmethod
    def _pence(value):
        return int(Decimal(str(value).replace("£", "").replace(",", "")) * 100)

    @staticmethod
    def _lines(page):
        # Phrases, not single words: spaces stay inside a phrase and a gap wider than half the text size ends it, which
        # separates the statement's columns.
        lines = [Line(w["text"].strip(), w["x0"], w["top"], w["x1"], w["bottom"]) for w in page.phrases(0.5) if w["text"].strip()]
        return sorted(lines, key=lambda line: (round(line.y0, 1), line.x0))

    @staticmethod
    def _same_row(lines, y):
        return [line for line in lines if abs(line.y0 - y) < 1]

    def _summary(self, lines):
        starts = [line for line in lines if line.text.lower() == "balance summary"]
        ends = [line.y0 for line in lines if self.SECTION.fullmatch(line.text)]
        if len(starts) != 1 or not ends:
            raise ValueError("Revolut balance summary or first account section is missing")
        body = [line for line in lines if starts[0].y0 < line.y0 < min(ends)]
        headers = {line.text.lower(): line for line in body if line.text.lower() in ("product", "opening balance", "money out", "money in")}
        if len(headers) != 4 or not any(line.text.lower().startswith("closing") for line in body):
            raise ValueError("Incomplete Revolut balance-summary columns")
        cash_lines = [line for line in body if self.MONEY.fullmatch(line.text)]
        ys = sorted({round(line.y0, 1) for line in cash_lines})
        controls = []
        total = None
        for i, y in enumerate(ys):
            money = sorted(self._same_row(cash_lines, y), key=lambda line: line.x0)
            if len(money) != 4:
                raise ValueError("A Revolut balance-summary row does not contain four amounts")
            stop = ys[i + 1] if i + 1 < len(ys) else min(ends)
            label_lines = [line.text for line in body if y - 1 <= line.y0 < stop - 1 and line.x1 < money[0].x0]
            if not label_lines:
                raise ValueError("Unlabelled Revolut balance-summary row")
            product = label_lines[0]
            named = [text for text in label_lines[1:] if text != "Where your transactions are remitted"]
            values = dict(zip(("opening_balance", "money_out", "money_in", "closing_balance"), [self._pence(line.text) for line in money]))
            if product.lower() == "total":
                total = values
                continue
            if "pocket" in product.lower():
                account = product
            elif product.lower().startswith("account"):
                if len(named) > 1:
                    raise ValueError("Ambiguous linked-account name in Revolut balance summary")
                account = named[0] if named else "Main account"
            else:
                raise ValueError(f"Unsupported Revolut product: {product}")
            if any(row["account_name"] == account for row in controls):
                raise ValueError(f"Duplicate Revolut account identity: {account}")
            controls.append({"account_name": account, "product": product, **values})
        if not controls or total is None:
            raise ValueError("Revolut balance-summary totals are missing")
        for key in total:
            if sum(row[key] for row in controls) != total[key]:
                raise ValueError(f"Revolut summary {key} does not equal its account rows")
        return controls, total

    @staticmethod
    def _account(title, controls):
        names = [row["account_name"] for row in controls]
        if title == "Account" and "Main account" in names:
            return "Main account"
        exact = [name for name in names if name.casefold() == title.casefold()]
        if len(exact) == 1:
            return exact[0]
        short = re.sub(r"['’]s account$", "", title, flags=re.IGNORECASE).casefold()
        matches = [name for name in names if name.casefold() == short or name.casefold().startswith(short + " ")]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Cannot uniquely match Revolut section to summary: {title}")

    @staticmethod
    def _headers(lines, date_header):
        row = {line.text.lower(): line for line in lines if abs(line.y0 - date_header.y0) < 1}
        required = ("description", "money out", "money in")
        if any(name not in row for name in required):
            raise ValueError("Incomplete Revolut transaction column headers")
        return {"date": date_header, **{name: row[name] for name in required}, **({"balance": row["balance"]} if "balance" in row else {})}

    def _metadata(self, first, controls, total, period):
        # Metadata labels are matched geometrically, never from transaction descriptions.
        def beside(label, pattern):
            anchors = [line for line in first if line.text.casefold() == label.casefold()]
            matches = [line.text for anchor in anchors for line in first if abs(line.y0 - anchor.y0) < 1 and line.x0 > anchor.x1 and re.fullmatch(pattern, line.text)]
            return matches[0] if len(matches) == 1 else None
        holder = None
        summary_y = next(line.y0 for line in first if line.text.lower() == "balance summary")
        account_heading = next((line for line in first if line.text == "IBAN"), None)
        if account_heading:
            candidates = [line for line in first if line.y0 < summary_y and line.y0 < account_heading.y0 and line.x0 < account_heading.x0 and line.text.isupper() and not line.text.endswith("Statement")]
            if len(candidates) == 1:
                holder = candidates[0].text
        account = beside("Account Number", r"\d{6,12}")
        sort_code = beside("Sort Code", r"\d{2}[- ]?\d{2}[- ]?\d{2}")
        return Statement(bank_name="revolut", account_number=account or "Not stated", account_holder=holder,
                         sort_code=sort_code, currency="GBP", statement_start_date=period[0], statement_end_date=period[1],
                         opening_balance=total["opening_balance"] / 100, closing_balance=total["closing_balance"] / 100)

    def parse_pdf(self, file_path: Path) -> ExtractionResult:
        try:
            pages = read_pages(file_path)
        except Exception as exc:  # encrypted or unreadable
            raise ValueError(f"Revolut PDF cannot be opened: {exc}") from exc
        if not pages:
            raise ValueError("Revolut PDF has no pages")
        first = self._lines(pages[0])
        if not any(line.text == "GBP Statement" for line in first):
            raise ValueError("This Revolut parser supports native GBP account statements only")
        controls, total = self._summary(first)
        transactions, excluded, coverage, issues = [], [], [], []
        current_account = None
        completed_account = None
        period = None
        reverted = False
        seen_accounts = set()
        for page_number, page in enumerate(pages, 1):
            lines = self._lines(page)
            footer = min([line.y0 for line in lines if line.text.startswith(("Report lost or stolen card", "Your Retail current account", "©"))] or [page.height]) - 1
            events = []
            for line in lines:
                section = self.SECTION.fullmatch(line.text)
                undo = self.REVERTED.fullmatch(line.text)
                if section:
                    events.append((line.y0, "section", section))
                elif undo:
                    events.append((line.y0, "reverted", undo))
                elif line.text in ("Date", "Start date"):
                    events.append((line.y0, "header", line))
            events.sort(key=lambda event: event[0])
            page_count = excluded_count = anchor_count = 0
            used_money = set()
            table_start = None
            for i, (y, kind, value) in enumerate(events):
                if kind in ("section", "reverted"):
                    date_values = value.groups()[-2:]
                    this_period = tuple(self._date(date) for date in date_values)
                    if period is None:
                        period = this_period
                    if period != this_period or this_period[0] > this_period[1]:
                        raise ValueError(f"Inconsistent Revolut section period on page {page_number}")
                    if kind == "section":
                        current_account = self._account(value.group(1), controls)
                        completed_account = current_account
                        seen_accounts.add(current_account)
                        reverted = False
                    else:
                        if completed_account is None:
                            raise ValueError("Reverted entries have no preceding account")
                        current_account = completed_account
                        reverted = True
                    continue
                header = self._headers(lines, value)
                if current_account is None or period is None:
                    raise ValueError(f"Table has no account section on page {page_number}")
                if ("balance" in header) == reverted:
                    raise ValueError(f"Unexpected balance column for Revolut table on page {page_number}")
                stop = min(events[i + 1][0] if i + 1 < len(events) else footer, footer)
                if table_start is None:
                    table_start = y
                table_lines = [line for line in lines if y + 1 < line.y0 < stop]
                date_right = (header["date"].x1 + header["description"].x0) / 2
                anchors = [line for line in table_lines if line.x0 < date_right and self.DATE.fullmatch(line.text)]
                # Date-like text in the date column is never silently discarded.
                malformed = [line for line in table_lines if line.x0 < date_right and re.match(r"^\d", line.text) and not self.DATE.fullmatch(line.text)]
                if malformed:
                    raise ValueError(f"Unrecognized transaction date on page {page_number}: {malformed[0].text}")
                for n, anchor in enumerate(anchors):
                    anchor_count += 1
                    end = anchors[n + 1].y0 if n + 1 < len(anchors) else stop
                    same = self._same_row(table_lines, anchor.y0)
                    money = {}
                    amount_headers = {key: header[key] for key in ("money out", "money in", "balance") if key in header}
                    for line in same:
                        if not self.MONEY.fullmatch(line.text):
                            continue
                        center = (line.x0 + line.x1) / 2
                        key = min(amount_headers, key=lambda key: abs(center - (amount_headers[key].x0 + amount_headers[key].x1) / 2))
                        if key in money:
                            raise ValueError(f"Duplicate amount column on page {page_number}")
                        money[key] = self._pence(line.text)
                        used_money.add(line)
                    if len(set(money) & {"money in", "money out"}) != 1 or (not reverted and "balance" not in money):
                        raise ValueError(f"Incomplete transaction amounts on page {page_number} at {anchor.text}")
                    desc_min = header["description"].x0 - 1
                    desc_max = header["money out"].x0 - 1
                    desc = [line.text for line in same if desc_min <= line.x0 < desc_max and not self.MONEY.fullmatch(line.text)]
                    detail_lines = [line.text for line in table_lines if anchor.y0 + 1 < line.y0 < end - 0.1 and desc_min <= line.x0 < desc_max]
                    continuation_money = [line for line in table_lines
                                          if anchor.y0 + 1 < line.y0 < end - 0.1
                                          and self.MONEY.fullmatch(line.text)]
                    for amount in continuation_money:
                        fee_lines = [line for line in table_lines
                                     if abs(line.y0 - amount.y0) < 1
                                     and line.text.startswith('Fee:')]
                        if len(fee_lines) != 1:
                            raise ValueError(f"Unexplained amount within transaction on page {page_number}")
                        fee = re.fullmatch(r'Fee:\s*(£[\d,]+\.\d{2})', fee_lines[0].text)
                        gross = money.get('money out', money.get('money in'))
                        if fee is None or self._pence(amount.text) + self._pence(fee.group(1)) != gross:
                            raise ValueError(f"Fee breakdown does not match transaction amount on page {page_number}")
                        detail_lines.append(f"Amount before fee: {amount.text}")
                        used_money.add(amount)
                    if not desc:
                        raise ValueError(f"Missing Revolut description on page {page_number}")
                    date = self._date(anchor.text)
                    if not period[0] <= date <= period[1]:
                        raise ValueError(f"Transaction outside statement period on page {page_number}")
                    txn = Transaction(date=date, description=" ".join(desc), details=" | ".join(detail_lines),
                                      money_in=money.get("money in", 0) / 100, money_out=money.get("money out", 0) / 100,
                                      balance=money["balance"] / 100 if "balance" in money else None,
                                      page_number=page_number, currency="GBP", date_source="statement",
                                      account_name=current_account, status="Reverted" if reverted else "Completed")
                    if reverted:
                        excluded.append(txn)
                        excluded_count += 1
                    else:
                        transactions.append(txn)
                        page_count += 1
            if table_start is not None:
                unassigned = [line for line in lines if table_start < line.y0 < footer and self.MONEY.fullmatch(line.text) and line not in used_money]
                if unassigned:
                    raise ValueError(f"Unassigned monetary row on page {page_number}: {unassigned[0].text}")
            coverage.append({"page": page_number, "date_anchors": anchor_count, "completed_rows": page_count, "reverted_rows": excluded_count})
        for control in controls:
            account = control["account_name"]
            txns = [txn for txn in transactions if txn.account_name == account]
            if account not in seen_accounts:
                raise ValueError(f"Account from balance summary has no parsed section: {account}")
            current = control["opening_balance"]
            differences = []
            for txn in txns:
                current += self._pence(txn.money_in) - self._pence(txn.money_out)
                diff = self._pence(txn.balance) - current
                if diff:
                    differences.append({"page": txn.page_number, "date": txn.date.isoformat()[:10], "difference": diff / 100})
            actual_in = sum(self._pence(txn.money_in) for txn in txns)
            actual_out = sum(self._pence(txn.money_out) for txn in txns)
            matched = actual_in == control["money_in"] and actual_out == control["money_out"] and current == control["closing_balance"] and not differences
            if not matched:
                issues.append(f"{account}: extracted totals or running balances do not match the statement")
            if txns:
                validation = BalanceValidator(tolerance=0.0001).validate_transactions(txns, control["opening_balance"] / 100)
                if not validation.success and matched:
                    issues.append(f"{account}: {validation.message}")
            if any(b.date < a.date for a, b in zip(txns, txns[1:])):
                issues.append(f"{account}: transaction dates are not chronological")
            control.update(transaction_count=len(txns), extracted_money_in=actual_in / 100, extracted_money_out=actual_out / 100,
                           calculated_closing=current / 100, balance_mismatches=differences, reconciled=matched,
                           first_page=min((txn.page_number for txn in txns), default=None), last_page=max((txn.page_number for txn in txns), default=None))
            for key in ("opening_balance", "money_out", "money_in", "closing_balance"):
                control[key] /= 100
        statement = self._metadata(first, controls, total, period)
        result = ExtractionResult(statement=statement, transactions=transactions, success=not issues,
                                  balance_reconciled=not issues, confidence_score=100.0 if not issues else 0.0,
                                  extraction_method="native_pdf_revolut", warnings=issues,
                                  error_message="; ".join(issues) if issues else None,
                                  account_summaries=controls, excluded_transactions=excluded, page_coverage=coverage,
                                  source_file=Path(file_path).name, source_sha256=hashlib.sha256(Path(file_path).read_bytes()).hexdigest())
        return result

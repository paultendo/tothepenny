# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Copyright (C) 2026 Paul Wood FRSA. tothepenny by Paul Wood FRSA; see NOTICE and COMMERCIAL.md.

"""Universal layout-driven parser for bank statements."""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base_parser import BaseTransactionParser
from ..models import Transaction
from ..utils import parse_currency, parse_date, infer_year_from_period, looks_like_date

logger = logging.getLogger(__name__)


@dataclass
class ColumnModel:
    """Inferred column positions for layout parsing."""
    centers: List[float]
    boundaries: List[float]
    money_out_idx: Optional[int]
    money_in_idx: Optional[int]
    balance_idx: Optional[int]
    amount_min_x: float
    date_x_max: float
    desc_min_x: float
    desc_max_x: float
    decimal_ratio: Optional[List[float]] = None


class UniversalLayoutParser(BaseTransactionParser):
    """Bank-agnostic layout parser driven by word coordinates."""

    MONTH_TRANSLATIONS = {
        # English
        "jan": "Jan", "january": "Jan",
        "feb": "Feb", "february": "Feb",
        "mar": "Mar", "march": "Mar",
        "apr": "Apr", "april": "Apr",
        "may": "May",
        "jun": "Jun", "june": "Jun",
        "jul": "Jul", "july": "Jul",
        "aug": "Aug", "august": "Aug",
        "sep": "Sep", "sept": "Sep", "september": "Sep",
        "oct": "Oct", "october": "Oct",
        "nov": "Nov", "november": "Nov",
        "dec": "Dec", "december": "Dec",
        # Portuguese
        "janeiro": "Jan",
        "fev": "Feb", "fevereiro": "Feb",
        "marco": "Mar",
        "abr": "Apr", "abril": "Apr",
        "maio": "May",
        "junho": "Jun",
        "julho": "Jul",
        "ago": "Aug", "agosto": "Aug",
        "set": "Sep", "setembro": "Sep",
        "out": "Oct", "outubro": "Oct",
        "novembro": "Nov",
        "dez": "Dec", "dezembro": "Dec",
        # French
        "janv": "Jan", "janvier": "Jan",
        "fev": "Feb", "fevr": "Feb", "fevrier": "Feb",
        "mars": "Mar",
        "avr": "Apr", "avril": "Apr",
        "mai": "May",
        "juin": "Jun",
        "juil": "Jul", "juillet": "Jul",
        "aou": "Aug", "aout": "Aug",
        "sept": "Sep", "septembre": "Sep",
        "octobre": "Oct",
        "novembre": "Nov",
        "decembre": "Dec",
    }
    MAX_DATE_ANCHOR_ROWS = 6
    HEADER_KEYWORDS = [
        "date", "description", "details", "balance",
        "paid in", "paid out", "money in", "money out",
        "credit", "debit", "withdrawn", "deposited",
        "paidin", "paidout"
    ]

    SUMMARY_KEYWORDS = [
        "total", "totals", "opening balance", "closing balance",
        "balance brought forward", "balance carried forward",
        "brought forward", "carried forward",
        "previous balance", "new balance",
        "account summary", "statement summary",
        "payments in", "payments out",
        "total payments", "total paid in", "total paid out",
        "total credits", "total debits",
        "saldo do dia", "saldo", "start balance", "end balance"
    ]

    NOTE_KEYWORDS = [
        "overdraft limit", "overdraft rate",
        "debit interest details", "interest arranged", "interest unarranged",
        "interest rate", "apr", "aer",
        "charging period", "charging periods",
        "arrangements period", "arrangements periods"
    ]

    NOTE_BLOCK_HEADERS = [
        "debit interest details", "interest details",
        "charging period", "charging periods",
        "arrangements period", "arrangements periods",
        "overdraft limit", "overdraft rate"
    ]

    BALANCE_MARKERS = [
        "balance brought forward",
        "balance carried forward",
        "brought forward",
        "carried forward",
        "opening balance",
        "closing balance",
        "saldo do dia",
        "start balance",
        "end balance"
    ]

    CURRENCY_SYMBOLS = {"£", "$", "€", "¥", "₹", "R$"}
    CREDIT_MARKERS = {"CR", "DR", "DB", "OD"}
    AMOUNT_PATTERN = re.compile(
        r'(?<!\d)(?:[£$€¥₹]|R\$)?-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})(?:\s?(?:CR|DR|DB|OD))?(?!\d)',
        re.IGNORECASE
    )

    def parse_transactions(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Transaction]:
        if not self.word_layout:
            raise ValueError("Word layout is required for UniversalLayoutParser")

        rows = self._build_layout_rows(self.word_layout)
        if not rows:
            logger.warning("Universal parser: no layout rows")
            return []

        page_bounds = self._detect_table_bounds(rows)
        table_rows = self._filter_rows_to_table(rows, page_bounds)
        if not table_rows:
            logger.warning("Universal parser: no table rows after filtering")
            return []

        model = self._infer_column_model(table_rows, statement_start_date, statement_end_date)
        entries = self._extract_entries(
            table_rows,
            model,
            statement_start_date,
            statement_end_date
        )
        if not entries:
            logger.warning("Universal parser: no transaction entries extracted")
            return []

        self._apply_direction_inference(entries, model)
        transactions = self._build_transactions(entries)

        logger.info("Universal parser extracted %d transactions", len(transactions))
        return transactions

    # ---- Row construction helpers ----

    def _build_layout_rows(self, word_layout: list, y_tolerance: float = 1.3) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for page in word_layout:
            words = [w for w in page.get("words", []) if (w.get("text") or "").strip()]
            if not words:
                continue

            sorted_words = sorted(words, key=lambda w: (w.get("top", 0.0), w.get("x0", 0.0)))
            current_row: List[dict] = []
            current_top: Optional[float] = None

            def flush_row() -> None:
                if not current_row:
                    return
                row_bottom = max(
                    (word.get("bottom") or word.get("top") or 0.0)
                    for word in current_row
                )
                rows.append({
                    "page": page.get("page_number"),
                    "top": current_top,
                    "bottom": row_bottom,
                    "words": list(current_row),
                    "text": " ".join(word.get("text", "").strip() for word in current_row).strip()
                })

            for word in sorted_words:
                top = word.get("top", 0.0)
                if current_row and current_top is not None and abs(top - current_top) > y_tolerance:
                    flush_row()
                    current_row = [word]
                    current_top = top
                else:
                    if not current_row:
                        current_top = top
                    current_row.append(word)

            flush_row()

        return rows

    def _detect_table_bounds(self, rows: List[Dict[str, Any]]) -> Dict[int, Dict[str, float]]:
        bounds: Dict[int, Dict[str, float]] = {}

        rows_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for row in rows:
            page = row.get("page")
            if page is None:
                continue
            rows_by_page.setdefault(page, []).append(row)

        for page, page_rows in rows_by_page.items():
            page_rows = sorted(page_rows, key=lambda r: r.get("top", 0.0))
            header_idx = None
            dense_indices: List[int] = []

            for idx, row in enumerate(page_rows):
                row_text = row.get("text", "")
                if self._is_header_row_text(row_text) and header_idx is None:
                    header_idx = idx

                if self._is_skip_line(row_text) or self._is_summary_row(row_text):
                    continue

                amount_count, rightmost_x1 = self._row_amount_signal(row)
                if amount_count >= 2:
                    dense_indices.append(idx)
                elif amount_count == 1 and rightmost_x1 is not None and rightmost_x1 > 250:
                    dense_indices.append(idx)

            if dense_indices:
                first_dense = dense_indices[0]
                last_dense = dense_indices[-1]
                start_idx = first_dense
                if header_idx is not None and header_idx <= last_dense:
                    start_idx = header_idx

                top = page_rows[start_idx].get("top")
                bottom = page_rows[last_dense].get("bottom", page_rows[last_dense].get("top"))

                # Include balance markers immediately after the last dense row
                if bottom is not None:
                    for follow_row in page_rows[last_dense + 1:]:
                        follow_top = follow_row.get("top")
                        if follow_top is None or follow_top - bottom > 30:
                            break
                        if self._is_balance_marker_text(follow_row.get("text", "")):
                            bottom = follow_row.get("bottom", follow_top)
                            break
                bounds[page] = {"top": top, "bottom": bottom}
            elif header_idx is not None:
                top = page_rows[header_idx].get("top")
                bottom = page_rows[-1].get("bottom", page_rows[-1].get("top"))
                bounds[page] = {"top": top, "bottom": bottom}

        return bounds

    def _row_amount_signal(self, row: Dict[str, Any]) -> Tuple[int, Optional[float]]:
        words = row.get("words", [])
        tokens = self._extract_amount_tokens(words, amount_min_x=None)
        if tokens:
            rightmost = max(token["x1"] for token in tokens)
            return len(tokens), rightmost

        row_text = row.get("text", "")
        matches = list(self.AMOUNT_PATTERN.finditer(row_text or ""))
        if matches:
            return len(matches), None

        return 0, None

    def _filter_rows_to_table(
        self,
        rows: List[Dict[str, Any]],
        bounds: Dict[int, Dict[str, float]],
        margin: float = 2.0
    ) -> List[Dict[str, Any]]:
        if not bounds:
            return rows

        filtered: List[Dict[str, Any]] = []
        for row in rows:
            page = row.get("page")
            if page not in bounds:
                continue
            row_top = row.get("top")
            row_bottom = row.get("bottom", row_top)
            bound = bounds[page]
            if row_bottom is not None and bound.get("top") is not None and row_bottom < bound["top"] - margin:
                continue
            if row_top is not None and bound.get("bottom") is not None and row_top > bound["bottom"] + margin:
                continue
            filtered.append(row)

        return filtered

    # ---- Column inference ----

    def _infer_column_model(
        self,
        rows: List[Dict[str, Any]],
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> ColumnModel:
        date_x_max = self._detect_date_x_max(rows)
        min_amount_x = date_x_max + 12.0

        amount_tokens: List[dict] = []
        for row in rows:
            amount_tokens.extend(self._extract_amount_tokens(row.get("words", []), amount_min_x=None))

        amount_tokens = [
            t for t in amount_tokens
            if t["x0"] >= min_amount_x
            or self._has_currency_symbol(t["text"])
            or self._has_decimal(t["text"])
        ]

        decimal_tokens = [t for t in amount_tokens if self._has_decimal(t["text"])]
        if len(decimal_tokens) >= 3:
            amount_tokens = decimal_tokens
        else:
            amount_tokens = [
                t for t in amount_tokens
                if t["x0"] >= max(160.0, min_amount_x)
                or self._has_decimal(t["text"])
                or self._has_currency_symbol(t["text"])
            ]

        positions = [token["x1"] for token in amount_tokens]
        if not positions:
            centers = [400.0, 470.0, 540.0]
            boundaries = [(centers[0] + centers[1]) / 2.0, (centers[1] + centers[2]) / 2.0]
            amount_min_x = min(centers) - 12.0
            desc_min_x = max(date_x_max + 6.0, 0.0)
            desc_max_x = amount_min_x - 6.0
            if desc_max_x <= desc_min_x:
                desc_max_x = desc_min_x + 220.0
            return ColumnModel(
                centers=centers,
                boundaries=boundaries,
                money_out_idx=0,
                money_in_idx=1,
                balance_idx=2,
                amount_min_x=amount_min_x,
                date_x_max=date_x_max,
                desc_min_x=desc_min_x,
                desc_max_x=desc_max_x
            )

        header_hint = self._detect_header_hints(rows)

        best_model: Optional[ColumnModel] = None
        best_score = float("-inf")

        balance_center = self._infer_balance_center(rows, min_amount_x, amount_tokens, header_hint)

        center_sets: List[List[float]] = []
        if balance_center is not None:
            amount_positions = [
                token["x1"] for token in amount_tokens
                if abs(token["x1"] - balance_center) > 12.0
            ]
            if amount_positions:
                amount_centers = self._detect_amount_centers(amount_positions)
                if len(amount_centers) >= 2:
                    center_sets.append(sorted([balance_center] + amount_centers[:2]))
                else:
                    if len(amount_positions) >= 2:
                        clusters = self._kmeans_positions(amount_positions, 2)
                        amount_centers = [self._median(cluster) for cluster in clusters]
                        center_sets.append(sorted([balance_center] + amount_centers))
                    center_sets.append(sorted([balance_center, self._median(amount_positions)]))

        if not center_sets:
            k_candidates = [k for k in (3, 2, 1) if len(positions) >= k]
            for k in k_candidates:
                clusters = self._kmeans_positions(positions, k)
                centers = [self._median(cluster) for cluster in clusters]
                centers.sort()
                center_sets.append(centers)

        for centers in center_sets:
            k = len(centers)
            if k == 0:
                continue

            boundaries: List[float] = []
            for left, right in zip(centers, centers[1:]):
                boundaries.append((left + right) / 2.0)

            amount_min_x = min(centers) - 12.0 if centers else 300.0
            desc_min_x = max(date_x_max + 6.0, 0.0)
            desc_max_x = amount_min_x - 6.0
            if desc_max_x <= desc_min_x:
                desc_max_x = desc_min_x + 220.0

            decimal_ratio = self._decimal_ratios(amount_tokens, centers)
            balance_idx_hint = None
            if balance_center is not None:
                balance_idx_hint = min(range(len(centers)), key=lambda i: abs(centers[i] - balance_center))

            role_candidates: List[Tuple[Optional[int], Optional[int], Optional[int]]] = []
            if k == 3:
                if balance_idx_hint is not None:
                    remaining = [idx for idx in range(3) if idx != balance_idx_hint]
                    role_candidates = [
                        (remaining[0], remaining[1], balance_idx_hint),
                        (remaining[1], remaining[0], balance_idx_hint)
                    ]
                else:
                    role_candidates = [(0, 1, 2), (1, 0, 2)]
            elif k == 2:
                if balance_idx_hint is not None:
                    other_idx = 1 - balance_idx_hint
                    role_candidates = [(other_idx, None, balance_idx_hint)]
                else:
                    role_candidates = [(0, 1, None), (1, 0, None), (0, None, 1), (1, None, 0)]
            else:
                role_candidates = [(0, None, None)]

            for money_out_idx, money_in_idx, balance_idx in role_candidates:
                model = ColumnModel(
                    centers=centers,
                    boundaries=boundaries,
                    money_out_idx=money_out_idx,
                    money_in_idx=money_in_idx,
                    balance_idx=balance_idx,
                    amount_min_x=amount_min_x,
                    date_x_max=date_x_max,
                    desc_min_x=desc_min_x,
                    desc_max_x=desc_max_x,
                    decimal_ratio=decimal_ratio
                )
                score = self._score_column_assignment(
                    rows,
                    model,
                    statement_start_date,
                    statement_end_date,
                    header_hint
                )
                if score > best_score:
                    best_score = score
                    best_model = model

        if best_model is None:
            best_model = ColumnModel(
                centers=centers,
                boundaries=boundaries,
                money_out_idx=0,
                money_in_idx=1 if len(centers) > 1 else None,
                balance_idx=2 if len(centers) > 2 else None,
                amount_min_x=min(centers) - 12.0 if centers else 300.0,
                date_x_max=date_x_max,
                desc_min_x=max(date_x_max + 6.0, 0.0),
                desc_max_x=min(centers) - 6.0 if centers else 520.0,
                decimal_ratio=self._decimal_ratios(amount_tokens, centers)
            )

        logger.info(
            "Universal parser column model: centers=%s, money_out=%s, money_in=%s, balance=%s",
            [round(c, 1) for c in best_model.centers],
            best_model.money_out_idx,
            best_model.money_in_idx,
            best_model.balance_idx
        )

        return best_model

    def _assign_column_roles(
        self,
        rows: List[Dict[str, Any]],
        centers: List[float],
        boundaries: List[float]
    ) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        if not centers:
            return None, None, None

        if len(centers) >= 3:
            return 0, 1, 2

        if len(centers) == 1:
            return 0, None, None

        # Two-column ambiguity: money in/out vs money+balance
        both_columns = 0
        row_count = 0
        for row in rows:
            tokens = self._extract_amount_tokens(row.get("words", []), amount_min_x=None)
            tokens = [
                t for t in tokens
                if t["x0"] >= 160
                or self._has_decimal(t["text"])
                or self._has_currency_symbol(t["text"])
            ]
            if not tokens:
                continue
            row_count += 1
            seen = set()
            for token in tokens:
                idx = self._assign_column_index(token["x1"], boundaries, len(centers))
                seen.add(idx)
            if len(seen) >= 2:
                both_columns += 1

        ratio = both_columns / row_count if row_count else 0.0
        if ratio >= 0.5:
            return 0, None, 1

        return 0, 1, None

    def _detect_date_x_max(self, rows: List[Dict[str, Any]]) -> float:
        header_date_x = None
        candidate_positions: List[float] = []

        for row in rows:
            text = row.get("text", "").lower()
            if "date" in text and self._is_header_row_text(text):
                for word in row.get("words", []):
                    if (word.get("text") or "").lower().startswith("date"):
                        header_date_x = word.get("x1", header_date_x)
                        break
                if header_date_x:
                    break

        for row in rows:
            for word in row.get("words", []):
                token = (word.get("text") or "").strip()
                if not token:
                    continue
                if word.get("x0", 0.0) > 140:
                    continue
                token_norm = self._strip_accents(token)
                if (
                    looks_like_date(token_norm)
                    or re.match(r'^\d{1,2}$', token_norm)
                    or self._looks_like_month_token(token_norm)
                    or re.match(r'^[A-Za-z]{3,}$', token_norm)
                ):
                    candidate_positions.append(word.get("x1", 0.0))

        if header_date_x:
            return float(header_date_x)

        if candidate_positions:
            candidate_positions.sort()
            return float(candidate_positions[int(0.9 * (len(candidate_positions) - 1))])

        return 90.0

    def _cluster_positions(
        self,
        positions: List[float],
        max_clusters: int = 3,
        gap: float = 18.0
    ) -> List[List[float]]:
        if not positions:
            return []
        sorted_pos = sorted(positions)
        clusters: List[List[float]] = []
        current = [sorted_pos[0]]
        for pos in sorted_pos[1:]:
            if pos - current[-1] > gap:
                clusters.append(current)
                current = [pos]
            else:
                current.append(pos)
        clusters.append(current)

        while len(clusters) > max_clusters:
            min_idx = 0
            min_dist = float("inf")
            for idx in range(len(clusters) - 1):
                dist = abs(self._median(clusters[idx + 1]) - self._median(clusters[idx]))
                if dist < min_dist:
                    min_dist = dist
                    min_idx = idx
            clusters[min_idx].extend(clusters[min_idx + 1])
            clusters.pop(min_idx + 1)

        return clusters

    def _kmeans_positions(self, positions: List[float], k: int, iterations: int = 10) -> List[List[float]]:
        if not positions:
            return []
        sorted_pos = sorted(positions)
        if k <= 1 or len(sorted_pos) == 1:
            return [sorted_pos]

        centers: List[float] = []
        for idx in range(k):
            quantile = (idx + 0.5) / k
            pos_idx = int(quantile * (len(sorted_pos) - 1))
            centers.append(sorted_pos[pos_idx])

        for _ in range(iterations):
            clusters: List[List[float]] = [[] for _ in range(k)]
            for pos in sorted_pos:
                nearest_idx = min(range(k), key=lambda c: abs(pos - centers[c]))
                clusters[nearest_idx].append(pos)

            new_centers = []
            for cluster, center in zip(clusters, centers):
                if cluster:
                    new_centers.append(self._median(cluster))
                else:
                    new_centers.append(center)

            if all(abs(a - b) < 0.1 for a, b in zip(centers, new_centers)):
                centers = new_centers
                break
            centers = new_centers

        return clusters

    def _score_column_assignment(
        self,
        rows: List[Dict[str, Any]],
        model: ColumnModel,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime],
        header_hint: Optional[Dict[str, float]] = None
    ) -> float:
        entries = self._extract_entries(
            rows,
            model,
            statement_start_date,
            statement_end_date
        )
        if not entries:
            return float("-inf")

        self._apply_direction_inference(entries, model)

        total = len(entries)
        dated = sum(1 for entry in entries if entry.get("date"))
        amountful = sum(
            1 for entry in entries
            if (entry.get("money_in") or 0) > 0
            or (entry.get("money_out") or 0) > 0
            or entry.get("amount_value") is not None
        )

        mismatch_ratio, checks = self._ledger_mismatch_ratio(entries)

        score = 0.0
        score += (amountful / total) * 40.0 if total else 0.0
        score += (dated / total) * 20.0 if total else 0.0
        if checks > 0:
            score += (1.0 - mismatch_ratio) * 40.0
        else:
            score += 10.0

        if total < 3:
            score -= 10.0

        if header_hint and model.centers:
            def closest_center(x: float) -> int:
                return min(range(len(model.centers)), key=lambda idx: abs(model.centers[idx] - x))

            if header_hint.get("out_x") is not None and model.money_out_idx is not None:
                if model.money_out_idx == closest_center(header_hint["out_x"]):
                    score += 6.0
                else:
                    score -= 3.0
            if header_hint.get("in_x") is not None and model.money_in_idx is not None:
                if model.money_in_idx == closest_center(header_hint["in_x"]):
                    score += 6.0
                else:
                    score -= 3.0
            if header_hint.get("balance_x") is not None and model.balance_idx is not None:
                if model.balance_idx == closest_center(header_hint["balance_x"]):
                    score += 6.0
                else:
                    score -= 3.0

            has_out = header_hint.get("out_x") is not None
            has_in = header_hint.get("in_x") is not None
            has_balance = header_hint.get("balance_x") is not None

            if has_out and model.money_out_idx is None:
                score -= 6.0
            if has_in and model.money_in_idx is None:
                score -= 6.0
            if has_balance and model.balance_idx is None:
                score -= 8.0
            if has_balance and model.balance_idx is not None:
                score += 4.0
            if has_in and has_out and not has_balance and model.balance_idx is not None:
                score -= 8.0

        return score

    def _detect_header_hints(self, rows: List[Dict[str, Any]]) -> Dict[str, float]:
        for row in rows:
            row_text = (row.get("text") or "").lower()
            if not self._is_header_row_text(row_text):
                continue
            words = row.get("words", [])
            tokens = [(word.get("text") or "").lower() for word in words]
            out_x = in_x = balance_x = None

            for idx, token in enumerate(tokens):
                if "balance" in token:
                    balance_x = words[idx].get("x1", balance_x)
                if token in {"out", "withdrawn"} or "paidout" in token:
                    out_x = words[idx].get("x1", out_x)
                if token in {"in", "credit"} or "paidin" in token:
                    in_x = words[idx].get("x1", in_x)

                if token in {"money", "paid"} and idx + 1 < len(tokens):
                    nxt = tokens[idx + 1]
                    if nxt == "out":
                        out_x = words[idx + 1].get("x1", out_x)
                    elif nxt == "in":
                        in_x = words[idx + 1].get("x1", in_x)

            return {"out_x": out_x, "in_x": in_x, "balance_x": balance_x}

        return {}

    def _ledger_mismatch_ratio(self, entries: List[Dict[str, Any]]) -> Tuple[float, int]:
        ordered = self._order_entries(entries)
        prev_balance: Optional[float] = None
        mismatches = 0
        checks = 0
        tolerance = getattr(self.config, "balance_tolerance", 0.01)

        for entry in ordered:
            balance = entry.get("balance")
            if balance is None:
                continue
            if prev_balance is None:
                prev_balance = balance
                continue
            if self._is_balance_marker_text(entry.get("description", "")):
                prev_balance = balance
                continue
            expected = prev_balance + (entry.get("money_in") or 0.0) - (entry.get("money_out") or 0.0)
            if abs(expected - balance) > tolerance:
                mismatches += 1
            checks += 1
            prev_balance = balance

        if checks == 0:
            return 1.0, 0

        return mismatches / checks, checks

    @staticmethod
    def _median(values: List[float]) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        mid = len(sorted_vals) // 2
        if len(sorted_vals) % 2 == 0:
            return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
        return sorted_vals[mid]

    def _decimal_ratios(self, tokens: List[dict], centers: List[float]) -> Optional[List[float]]:
        if not tokens or not centers:
            return None

        counts = [0] * len(centers)
        decimal_counts = [0] * len(centers)
        for token in tokens:
            idx = min(range(len(centers)), key=lambda i: abs(token["x1"] - centers[i]))
            counts[idx] += 1
            if self._has_decimal(token["text"]):
                decimal_counts[idx] += 1

        ratios = []
        for count, dec in zip(counts, decimal_counts):
            ratios.append(dec / count if count else 0.0)

        return ratios

    def _infer_balance_center(
        self,
        rows: List[Dict[str, Any]],
        min_amount_x: float,
        amount_tokens: List[dict],
        header_hint: Optional[Dict[str, float]] = None
    ) -> Optional[float]:
        balance_candidates: List[float] = []

        for row in rows:
            tokens = self._extract_amount_tokens(row.get("words", []), amount_min_x=None)
            tokens = [
                t for t in tokens
                if t["x0"] >= min_amount_x
                or self._has_currency_symbol(t["text"])
                or self._has_decimal(t["text"])
            ]
            if len(tokens) >= 2:
                rightmost = max(tokens, key=lambda t: t["x1"])
                balance_candidates.append(rightmost["x1"])

        if len(balance_candidates) >= 3:
            return self._median(balance_candidates)

        header_balance = None
        if header_hint:
            header_balance = header_hint.get("balance_x")

        if header_balance is not None:
            return float(header_balance)

        if balance_candidates:
            return self._median(balance_candidates)

        return None

    def _detect_amount_centers(self, positions: List[float]) -> List[float]:
        if not positions:
            return []

        bins: Dict[int, List[float]] = {}
        for pos in positions:
            key = int(round(pos / 10.0) * 10)
            bins.setdefault(key, []).append(pos)

        if not bins:
            return []

        sorted_bins = sorted(bins.items(), key=lambda kv: len(kv[1]), reverse=True)
        primary_key, primary_vals = sorted_bins[0]
        primary_center = self._median(primary_vals)
        centers = [primary_center]

        min_secondary = max(3, int(len(primary_vals) * 0.05))
        for key, vals in sorted_bins[1:]:
            if abs(key - primary_key) < 35:
                continue
            if len(vals) < min_secondary:
                continue
            centers.append(self._median(vals))
            break

        return centers

    # ---- Row extraction ----

    def _should_skip_row(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        row_text = row.get("text", "")
        if not row_text or not row_text.strip():
            return True

        if self._is_non_transaction_note_row(row, model):
            return True

        if self._is_summary_row(row_text) or self._is_date_range_row(row_text):
            return True

        if not self._is_skip_line(row_text):
            return False

        # If the row still looks like part of the transaction table, don't skip it
        if self._row_looks_like_transaction(row, model):
            return False

        return True

    def _row_looks_like_transaction(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        if not row or not model:
            return False

        if self._row_has_date_like_tokens(row, model):
            return True

        if self._row_has_amount_tokens(row, model):
            return True

        if self._row_has_description_tokens(row, model):
            return True

        return False

    def _is_non_transaction_note_row(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        row_text = row.get("text", "")
        if not row_text:
            return False

        if self._row_has_date_like_tokens(row, model):
            return False

        lower = row_text.lower()
        return any(keyword in lower for keyword in self.NOTE_KEYWORDS)

    def _is_note_block_header(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        row_text = row.get("text", "")
        if not row_text:
            return False

        if self._row_has_date_like_tokens(row, model):
            return False

        lower = row_text.lower()
        return any(keyword in lower for keyword in self.NOTE_BLOCK_HEADERS)

    def _row_has_date_like_tokens(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        tokens: List[str] = []
        for word in row.get("words", []):
            if word.get("x0", 0.0) < model.desc_min_x:
                token = (word.get("text") or "").strip()
                if token:
                    tokens.append(token)
            else:
                break

        if not tokens:
            return False

        tokens = tokens[:4]
        for span in range(len(tokens), 0, -1):
            candidate = self._normalize_spaces(" ".join(tokens[:span]))
            if candidate and looks_like_date(candidate):
                return True

        return False

    def _row_has_amount_tokens(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        tokens = self._extract_amount_tokens(
            row.get("words", []),
            amount_min_x=model.amount_min_x,
            column_boundaries=model.boundaries if model.boundaries else None
        )
        return bool(tokens)

    def _row_has_description_tokens(self, row: Dict[str, Any], model: ColumnModel) -> bool:
        for word in row.get("words", []):
            x0 = word.get("x0", 0.0)
            if model.desc_min_x <= x0 <= model.desc_max_x:
                token = (word.get("text") or "").strip()
                if token and not self._looks_like_amount(token):
                    return True
        return False

    def _extract_entries(
        self,
        rows: List[Dict[str, Any]],
        model: ColumnModel,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        pending_desc: List[str] = []
        pending_indent: Optional[float] = None
        pending_date_anchor = False
        note_block_active = False
        current_date: Optional[datetime] = None
        current_date_source: Optional[str] = None
        last_date_idx: Optional[int] = None
        last_date_page: Optional[int] = None

        for idx, row in enumerate(rows):
            row_text = row.get("text", "")
            if not row_text or not row_text.strip():
                continue

            if self._is_header_row_text(row_text):
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                note_block_active = False
                continue

            if self._is_balance_marker_text(row_text):
                balance_value = self._first_amount_value(row.get("words", []), model)
                marker_date = current_date or statement_start_date or statement_end_date
                date_source = "line" if current_date else "header"
                if balance_value is not None and marker_date:
                    entries.append({
                        "date": marker_date,
                        "description": row_text.strip(),
                        "money_in": 0.0,
                        "money_out": 0.0,
                        "balance": balance_value,
                        "amount_value": None,
                        "amount_negative": False,
                        "raw_text": row_text[:120],
                        "page": row.get("page"),
                        "date_source": date_source
                    })
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                note_block_active = False
                continue

            if note_block_active:
                if self._row_has_date_like_tokens(row, model):
                    note_block_active = False
                else:
                    continue

            if self._is_note_block_header(row, model):
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                note_block_active = True
                continue

            if self._should_skip_row(row, model):
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                note_block_active = False
                continue

            if self._is_date_range_row(row_text):
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                note_block_active = False
                continue

            row_date = self._extract_row_date(
                row.get("words", []),
                model,
                statement_start_date,
                statement_end_date
            )
            if row_date:
                current_date = row_date
                current_date_source = "line"
                pending_desc = []
                pending_indent = None
                pending_date_anchor = True
                last_date_idx = idx
                last_date_page = row.get("page")

            amount_info = self._extract_amounts_from_words(row.get("words", []), model)

            desc_fragment, desc_indent = self._extract_description_data(row.get("words", []), model)
            if desc_fragment:
                if pending_desc:
                    if pending_indent is None or desc_indent is None:
                        pending_desc.append(desc_fragment)
                    else:
                        allowed_gap = 12.0
                        if pending_date_anchor or amount_info["has_amount"]:
                            allowed_gap = 40.0
                        if abs(desc_indent - pending_indent) <= allowed_gap:
                            pending_desc.append(desc_fragment)
                        else:
                            pending_desc = [desc_fragment]
                            pending_indent = desc_indent
                else:
                    pending_desc = [desc_fragment]
                    pending_indent = desc_indent

            if not amount_info["has_amount"]:
                continue

            if not current_date:
                inline_date = self._extract_inline_date(row_text, statement_start_date, statement_end_date)
                if inline_date:
                    current_date = inline_date
                    current_date_source = "inline"
                    last_date_idx = idx
                    last_date_page = row.get("page")

            if current_date and not row_date and last_date_idx is not None:
                rows_since_date = idx - last_date_idx
                page = row.get("page")
                if rows_since_date > self.MAX_DATE_ANCHOR_ROWS:
                    current_date = None
                    current_date_source = None
                    pending_desc = []
                    pending_indent = None
                    pending_date_anchor = False
                elif last_date_page is not None and page is not None and page != last_date_page:
                    if rows_since_date > 1:
                        current_date = None
                        current_date_source = None
                        pending_desc = []
                        pending_indent = None
                        pending_date_anchor = False

            if not current_date:
                pending_desc = []
                pending_indent = None
                pending_date_anchor = False
                continue

            description = self._normalize_spaces(" ".join(pending_desc)) or self._normalize_spaces(desc_fragment or row_text)
            pending_desc = []
            pending_indent = None
            pending_date_anchor = False

            entries.append({
                "date": current_date,
                "description": description,
                "money_in": amount_info["money_in"],
                "money_out": amount_info["money_out"],
                "balance": amount_info["balance"],
                "amount_value": amount_info["amount_value"],
                "amount_negative": amount_info["amount_negative"],
                "amount_ambiguous": amount_info.get("amount_ambiguous", False),
                "raw_text": row_text[:120],
                "page": row.get("page"),
                "date_source": current_date_source or "line"
            })

        return entries

    def _extract_row_date(
        self,
        words: List[dict],
        model: ColumnModel,
        period_start: Optional[datetime],
        period_end: Optional[datetime]
    ) -> Optional[datetime]:
        tokens: List[str] = []
        for word in words:
            if word.get("x0", 0.0) < model.desc_min_x:
                tokens.append(word.get("text", ""))
            else:
                break
        if not tokens:
            return None

        tokens = tokens[:4]
        for span in range(len(tokens), 0, -1):
            candidate = self._normalize_spaces(" ".join(tokens[:span]))
            if not candidate:
                continue
            normalized = self._normalize_date_language(candidate)
            if not looks_like_date(normalized):
                continue
            parsed = self._parse_date_with_period(normalized, period_start, period_end)
            if parsed:
                return parsed

        return None

    def _parse_date_with_period(
        self,
        candidate: str,
        period_start: Optional[datetime],
        period_end: Optional[datetime]
    ) -> Optional[datetime]:
        candidate = self._normalize_date_language(candidate)
        if period_start and period_end:
            try:
                return infer_year_from_period(candidate, period_start, period_end)
            except Exception:
                return parse_date(candidate, self.config.date_formats)
        return parse_date(candidate, self.config.date_formats)

    def _extract_description_fragment(self, words: List[dict], model: ColumnModel) -> str:
        desc_words: List[str] = []
        for word in words:
            x0 = word.get("x0", 0.0)
            if model.desc_min_x <= x0 <= model.desc_max_x:
                token = (word.get("text") or "").strip()
                if token and not self._looks_like_amount(token):
                    desc_words.append(token)
        return self._normalize_spaces(" ".join(desc_words))

    def _extract_description_data(self, words: List[dict], model: ColumnModel) -> Tuple[str, Optional[float]]:
        desc_words: List[str] = []
        min_x0: Optional[float] = None

        for word in words:
            x0 = word.get("x0", 0.0)
            if model.desc_min_x <= x0 <= model.desc_max_x:
                token = (word.get("text") or "").strip()
                if token and not self._looks_like_amount(token):
                    desc_words.append(token)
                    if min_x0 is None or x0 < min_x0:
                        min_x0 = x0

        return self._normalize_spaces(" ".join(desc_words)), min_x0

    def _extract_amounts_from_words(self, words: List[dict], model: ColumnModel) -> Dict[str, Any]:
        result = {
            "money_in": None,
            "money_out": None,
            "balance": None,
            "amount_value": None,
            "amount_negative": False,
            "amount_ambiguous": False,
            "has_amount": False
        }

        tokens = self._extract_amount_tokens(
            words,
            amount_min_x=model.amount_min_x,
            column_boundaries=model.boundaries if model.boundaries else None
        )
        if tokens:
            tokens = [
                token for token in tokens
                if not (
                    token["x0"] < model.desc_min_x
                    and not self._has_decimal(token["text"])
                    and not self._has_currency_symbol(token["text"])
                    and (looks_like_date(token["text"]) or re.match(r'^\d{4}$', token["text"]))
                )
            ]
        if not tokens:
            return result

        for token in tokens:
            idx = self._assign_column_index(token["x1"], model.boundaries, len(model.centers))
            if model.decimal_ratio and idx < len(model.decimal_ratio):
                if not self._has_decimal(token["text"]) and not self._has_currency_symbol(token["text"]):
                    if model.decimal_ratio[idx] >= 0.5:
                        continue
            if model.balance_idx is not None and idx == model.balance_idx:
                if result["balance"] is None:
                    result["balance"] = token["value"]
                result["has_amount"] = True
                continue

            if model.money_out_idx is not None and idx == model.money_out_idx and model.money_in_idx is None:
                if result["amount_value"] is None:
                    result["amount_value"] = token["abs_value"]
                    result["amount_negative"] = token["negative"]
                    result["amount_ambiguous"] = not token["negative"]
                result["has_amount"] = True
                continue

            if model.money_out_idx is not None and idx == model.money_out_idx:
                if result["money_out"] is None:
                    result["money_out"] = token["abs_value"]
                result["has_amount"] = True
                continue

            if model.money_in_idx is not None and idx == model.money_in_idx:
                if result["money_in"] is None:
                    result["money_in"] = token["abs_value"]
                result["has_amount"] = True
                continue

        return result

    def _extract_amount_tokens(
        self,
        words: List[dict],
        amount_min_x: Optional[float],
        column_boundaries: Optional[List[float]] = None
    ) -> List[Dict[str, Any]]:
        tokens: List[Dict[str, Any]] = []
        merged = self._merge_amount_tokens(words, column_boundaries=column_boundaries)
        for token in merged:
            split_tokens = self._split_amount_token(token)
            for split in split_tokens:
                raw_text = (split.get("text") or "").strip()
                if not raw_text:
                    continue
                if not self._looks_like_amount(raw_text):
                    continue
                if amount_min_x is not None and split.get("x1", 0.0) < amount_min_x:
                    continue
                value = parse_currency(raw_text)
                if value is None:
                    continue
                tokens.append({
                    "text": raw_text,
                    "value": value,
                    "abs_value": abs(value),
                    "negative": value < 0,
                    "x0": split.get("x0", 0.0),
                    "x1": split.get("x1", 0.0)
                })
        return tokens

    def _merge_amount_tokens(
        self,
        words: List[dict],
        column_boundaries: Optional[List[float]] = None
    ) -> List[dict]:
        if not words:
            return []
        sorted_words = sorted(words, key=lambda w: w.get("x0", 0.0))
        char_width = self._estimate_char_width(sorted_words)
        max_gap = max(char_width * 1.6, 1.2)
        merged: List[dict] = []
        idx = 0
        while idx < len(sorted_words):
            word = sorted_words[idx]
            text = (word.get("text") or "").strip()
            next_word = sorted_words[idx + 1] if idx + 1 < len(sorted_words) else None

            if self._is_amount_fragment_start(text):
                combined_text = text
                x0 = word.get("x0", 0.0)
                x1 = word.get("x1", 0.0)
                j = idx + 1

                while j < len(sorted_words):
                    candidate = sorted_words[j]
                    cand_text = (candidate.get("text") or "").strip()
                    if not cand_text:
                        break

                    gap = candidate.get("x0", 0.0) - x1
                    if self._has_decimal(combined_text) and self._has_decimal(cand_text):
                        break

                    if self.AMOUNT_PATTERN.fullmatch(combined_text) and cand_text.upper() not in self.CREDIT_MARKERS and cand_text not in {")"}:
                        break

                    allowed_gap = max_gap
                    if cand_text in {",", "."}:
                        allowed_gap = max_gap * 2.0
                    if combined_text in self.CURRENCY_SYMBOLS:
                        allowed_gap = max_gap * 2.5

                    if gap > allowed_gap and cand_text not in {",", "."}:
                        break

                    if column_boundaries and cand_text not in {",", "."}:
                        if any(x1 < boundary < candidate.get("x0", 0.0) for boundary in column_boundaries):
                            break

                    if cand_text.upper() in self.CREDIT_MARKERS and self._looks_like_amount(combined_text):
                        combined_text += cand_text
                        x1 = candidate.get("x1", x1)
                        j += 1
                        break

                    if not self._is_amount_fragment(cand_text):
                        if cand_text == ")" and combined_text.startswith("("):
                            combined_text += cand_text
                            x1 = candidate.get("x1", x1)
                            j += 1
                        break

                    combined_text += cand_text
                    x1 = candidate.get("x1", x1)
                    j += 1

                merged.append({
                    "text": combined_text,
                    "x0": x0,
                    "x1": x1
                })
                idx = j
                continue

            merged.append({
                "text": text,
                "x0": word.get("x0", 0.0),
                "x1": word.get("x1", 0.0)
            })
            idx += 1

        return merged

    def _estimate_char_width(self, words: List[dict]) -> float:
        widths: List[float] = []
        for word in words:
            text = (word.get("text") or "").strip()
            if not text:
                continue
            if not re.search(r'\d', text) and text not in self.CURRENCY_SYMBOLS and text not in {",", ".", "-", "(", ")"}:
                continue
            x0 = word.get("x0", 0.0)
            x1 = word.get("x1", 0.0)
            length = max(len(text), 1)
            width = (x1 - x0) / length if x1 >= x0 else 0.0
            if width > 0:
                widths.append(width)

        if not widths:
            return 1.8

        widths.sort()
        return widths[len(widths) // 2]

    def _split_amount_token(self, token: dict) -> List[dict]:
        raw_text = (token.get("text") or "").strip()
        if not raw_text:
            return []

        matches = list(self.AMOUNT_PATTERN.finditer(raw_text))
        if len(matches) <= 1:
            return [token]

        length = len(raw_text)
        if length <= 0:
            return [token]

        x0 = float(token.get("x0", 0.0))
        x1 = float(token.get("x1", x0))
        width = max(x1 - x0, 0.0)

        split_tokens: List[dict] = []
        for match in matches:
            seg = match.group(0)
            start, end = match.span()
            seg_x0 = x0 + width * (start / length)
            seg_x1 = x0 + width * (end / length)
            split_tokens.append({
                "text": seg,
                "x0": seg_x0,
                "x1": seg_x1
            })

        return split_tokens or [token]

    def _is_amount_fragment_start(self, text: str) -> bool:
        if not text:
            return False
        if text in self.CURRENCY_SYMBOLS:
            return True
        if text in {"-", "("}:
            return True
        return self._is_amount_fragment(text)

    def _is_amount_fragment(self, text: str) -> bool:
        if not text:
            return False
        if text in self.CURRENCY_SYMBOLS:
            return True
        if text in {",", ".", "(", ")", "-"}:
            return True
        return bool(re.match(r'^[\d,\.]+$', text))

    def _apply_direction_inference(self, entries: List[Dict[str, Any]], model: ColumnModel) -> None:
        if not entries:
            return

        ordered_entries = self._order_entries(entries)
        prev_balance: Optional[float] = None

        for entry in ordered_entries:
            money_in = entry.get("money_in")
            money_out = entry.get("money_out")
            balance = entry.get("balance")

            if money_in is None:
                money_in = 0.0
            if money_out is None:
                money_out = 0.0

            if entry.get("amount_value") is not None and money_in == 0.0 and money_out == 0.0:
                amount = float(entry["amount_value"])
                if entry.get("amount_negative"):
                    money_out = abs(amount)
                elif prev_balance is not None and balance is not None:
                    delta = balance - prev_balance
                    if abs(delta - amount) <= abs(delta + amount):
                        money_in = abs(amount)
                    else:
                        money_out = abs(amount)
                else:
                    direction = self._classify_amount_by_keywords(entry.get("description", ""))
                    if direction == "paid_in":
                        money_in = abs(amount)
                    else:
                        money_out = abs(amount)

            if balance is None and prev_balance is not None and model.balance_idx is not None:
                balance = prev_balance + money_in - money_out
                entry["balance"] = balance

            entry["money_in"] = money_in
            entry["money_out"] = money_out

            if balance is not None:
                prev_balance = balance

        # Global optimization for ambiguous amount rows between known balances
        self._optimize_ambiguous_segments(ordered_entries)

        # Apply balance correction swaps where it improves ledger consistency
        prev_balance = None
        for entry in ordered_entries:
            balance = entry.get("balance")
            txn = Transaction(
                date=entry.get("date"),
                description=entry.get("description", ""),
                money_in=float(entry.get("money_in") or 0.0),
                money_out=float(entry.get("money_out") or 0.0),
                balance=balance,
                confidence=100.0
            )
            if prev_balance is not None:
                txn = self._validate_and_correct_balance(txn, prev_balance=prev_balance)
                entry["money_in"] = txn.money_in
                entry["money_out"] = txn.money_out
            if balance is not None:
                prev_balance = balance

    def _optimize_ambiguous_segments(self, entries: List[Dict[str, Any]]) -> None:
        prev_balance: Optional[float] = None
        prev_idx: Optional[int] = None

        for idx, entry in enumerate(entries):
            balance = entry.get("balance")
            if balance is None:
                continue

            if prev_balance is not None and prev_idx is not None and idx > prev_idx:
                segment = entries[prev_idx + 1: idx + 1]
                ambiguous = [
                    seg for seg in segment
                    if seg.get("amount_ambiguous")
                    and seg.get("amount_value") is not None
                    and not seg.get("amount_negative")
                ]

                if ambiguous:
                    fixed_delta = 0.0
                    for seg in segment:
                        if seg in ambiguous:
                            continue
                        fixed_delta += (seg.get("money_in") or 0.0) - (seg.get("money_out") or 0.0)

                    target_delta = balance - prev_balance
                    remaining = target_delta - fixed_delta

                    for amb in sorted(ambiguous, key=lambda e: abs(e.get("amount_value") or 0.0), reverse=True):
                        amount = float(amb.get("amount_value") or 0.0)
                        if remaining >= 0:
                            amb["money_in"] = amount
                            amb["money_out"] = 0.0
                            remaining -= amount
                        else:
                            amb["money_out"] = amount
                            amb["money_in"] = 0.0
                            remaining += amount

            prev_balance = balance
            prev_idx = idx

    def _order_entries(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        dated = [e for e in entries if e.get("date")]
        if len(dated) < 2:
            return entries

        increases = 0
        decreases = 0
        for prev, curr in zip(dated, dated[1:]):
            if curr["date"] > prev["date"]:
                increases += 1
            elif curr["date"] < prev["date"]:
                decreases += 1

        if decreases > increases:
            return list(reversed(entries))

        return entries

    def _build_transactions(self, entries: List[Dict[str, Any]]) -> List[Transaction]:
        transactions: List[Transaction] = []
        for entry in entries:
            date = entry.get("date")
            description = entry.get("description", "")
            money_in = float(entry.get("money_in") or 0.0)
            money_out = float(entry.get("money_out") or 0.0)
            balance = entry.get("balance")

            transaction = Transaction(
                date=date,
                description=description,
                money_in=money_in,
                money_out=money_out,
                balance=balance,
                transaction_type=self._detect_transaction_type(description),
                confidence=self._calculate_confidence(date, description, money_in, money_out, balance),
                raw_text=entry.get("raw_text"),
                page_number=entry.get("page"),
                date_source=entry.get("date_source")
            )
            transactions.append(transaction)

        return transactions

    # ---- Classification helpers ----

    def _is_header_row_text(self, text: str) -> bool:
        lower = (text or "").lower()
        hits = sum(1 for kw in self.HEADER_KEYWORDS if kw in lower)
        return hits >= 2

    def _is_summary_row(self, text: str) -> bool:
        lower = (text or "").lower()
        return any(keyword in lower for keyword in self.SUMMARY_KEYWORDS)

    def _is_balance_marker_text(self, text: str) -> bool:
        lower = (text or "").lower()
        return any(marker in lower for marker in self.BALANCE_MARKERS)

    def _is_date_range_row(self, text: str) -> bool:
        if not text:
            return False
        lower = text.lower()
        if " to " not in lower and "-" not in lower:
            return False
        date_like = re.findall(r'\d{1,2}\s+[A-Za-z]{3,}\s+\d{2,4}', text)
        if len(date_like) >= 2:
            return True
        if "statement" in lower and "to" in lower and date_like:
            return True
        if re.search(r'\d{1,2}/\d{1,2}/\d{2,4}\s+to\s+\d{1,2}/\d{1,2}/\d{2,4}', lower):
            return True
        return False

    def _extract_inline_date(
        self,
        text: str,
        statement_start_date: Optional[datetime],
        statement_end_date: Optional[datetime]
    ) -> Optional[datetime]:
        if not text:
            return None
        normalized_text = self._strip_accents(text)
        match = re.search(r'(\d{1,2}\s+[A-Z][a-z]+\s+\d{2,4})', normalized_text)
        if not match:
            compact = re.search(r'\b(\d{1,2})[.\-/ ]?([A-Za-z]{3,9})[.\-/ ]?(\d{2,4})?\b', normalized_text)
            if not compact:
                return None
            day, month_raw, year = compact.group(1), compact.group(2), compact.group(3)
            mapped = self._map_month_token(month_raw)
            if not mapped:
                return None
            candidate = f"{day} {mapped}"
            if year:
                candidate = f"{candidate} {year}"
            return self._parse_date_with_period(candidate, statement_start_date, statement_end_date)
        candidate = match.group(1)
        return self._parse_date_with_period(candidate, statement_start_date, statement_end_date)

    def _first_amount_value(self, words: List[dict], model: ColumnModel) -> Optional[float]:
        tokens = self._extract_amount_tokens(
            words,
            amount_min_x=model.amount_min_x,
            column_boundaries=model.boundaries if model.boundaries else None
        )
        if not tokens:
            return None
        # Prefer balance column if present
        if model.balance_idx is not None and model.centers:
            for token in tokens:
                idx = self._assign_column_index(token["x1"], model.boundaries, len(model.centers))
                if idx == model.balance_idx:
                    return token["value"]
        return tokens[0]["value"]

    def _looks_like_amount(self, token: str) -> bool:
        if not token:
            return False
        candidate = token.strip()
        if "/" in candidate:
            return False

        # Strip CR/DR markers
        candidate = re.sub(r'(?i)(CR|DR|DB|OD)$', '', candidate).strip()
        candidate = candidate.replace(" ", "")

        # Strip currency symbols
        if candidate.startswith("R$"):
            candidate = candidate[2:]
        for symbol in ["£", "$", "€", "¥", "₹"]:
            if candidate.startswith(symbol):
                candidate = candidate[len(symbol):]
        if candidate.startswith("(") and candidate.endswith(")"):
            candidate = candidate[1:-1]
        if candidate.startswith("-"):
            candidate = candidate[1:]

        if not candidate or not re.match(r'^\d[\d,\.]*$', candidate):
            return False

        # Reject internal minus signs
        if "-" in candidate:
            return False

        return True

    @staticmethod
    def _strip_accents(text: str) -> str:
        if not text:
            return ""
        return "".join(
            char for char in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(char)
        )

    def _normalize_date_language(self, candidate: str) -> str:
        if not candidate:
            return ""
        normalized = self._strip_accents(candidate)
        lower = normalized.lower()
        if not self.MONTH_TRANSLATIONS:
            return normalized
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(key) for key in self.MONTH_TRANSLATIONS.keys()) + r")\b",
            re.IGNORECASE
        )

        def _replace(match: re.Match) -> str:
            token = match.group(1).lower()
            return self.MONTH_TRANSLATIONS.get(token, match.group(0))

        return pattern.sub(_replace, lower)

    def _map_month_token(self, token: Optional[str]) -> Optional[str]:
        if not token:
            return None
        cleaned = self._strip_accents(token).lower().strip(".-/ ")
        if cleaned in self.MONTH_TRANSLATIONS:
            return self.MONTH_TRANSLATIONS[cleaned]
        token3 = cleaned[:3]
        return self.MONTH_TRANSLATIONS.get(token3)

    def _looks_like_month_token(self, token: str) -> bool:
        if not token:
            return False
        cleaned = self._strip_accents(token).lower().strip(".-/ ")
        return cleaned in self.MONTH_TRANSLATIONS

    @staticmethod
    def _has_decimal(token: str) -> bool:
        if not token:
            return False
        cleaned = token.strip()
        return bool(re.search(r'[.,]\d{2}\b', cleaned))

    @classmethod
    def _has_currency_symbol(cls, token: str) -> bool:
        if not token:
            return False
        return any(symbol in token for symbol in cls.CURRENCY_SYMBOLS)

    @staticmethod
    def _assign_column_index(x_pos: float, boundaries: List[float], count: int) -> int:
        if not boundaries:
            return 0
        for idx, boundary in enumerate(boundaries):
            if x_pos <= boundary:
                return idx
        return max(count - 1, 0)

    @staticmethod
    def _normalize_spaces(text: Optional[str]) -> str:
        return re.sub(r'\s+', ' ', text or "").strip()

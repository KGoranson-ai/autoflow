"""
Auto-Calculations for Spreadsheet Mode.

Scans the rows about to be typed and replaces trigger labels (total, sum,
average, avg, count, min, max) with their computed values before any
keystrokes are sent. Works entirely on the in-memory rows list — no
changes to the typing engine's timing or emit logic.

Trigger detection is case-insensitive and ignores surrounding whitespace
and common suffixes (colon, equals sign). Examples that all trigger:
    "total", "Total:", "TOTAL=", "sum", "SUM =", "total:"

Integration: call preprocess_rows(rows) at the top of type_spreadsheet()
before the typing loop.
"""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trigger label patterns
# ---------------------------------------------------------------------------

# Maps a canonical operation name to the set of label strings that trigger it.
# All matching is done after stripping and lowercasing the cell value.
_TRIGGER_MAP = {
    "sum":     {"total", "sum", "total:", "sum:", "total=", "sum=", "subtotal", "subtotal:"},
    "average": {"avg", "average", "mean", "avg:", "average:", "mean:"},
    "count":   {"count", "count:", "n=", "n:"},
    "min":     {"min", "min:", "minimum", "minimum:"},
    "max":     {"max", "max:", "maximum", "maximum:"},
}

# Reverse lookup: label string → operation
_LABEL_TO_OP: dict[str, str] = {}
for _op, _labels in _TRIGGER_MAP.items():
    for _label in _labels:
        _LABEL_TO_OP[_label] = _op


def _normalize_label(value: str) -> str:
    """Lowercase, strip whitespace and trailing punctuation for matching."""
    return re.sub(r"[\s=:]+$", "", value.strip().lower())


def _is_trigger(value: str) -> str | None:
    """Return the operation name if value is a trigger label, else None."""
    return _LABEL_TO_OP.get(_normalize_label(value))


def _collect_numeric_column(rows: List[List[str]], col_idx: int, stop_row: int) -> List[float]:
    """
    Collect all numeric values in column col_idx from row 0 up to (not including) stop_row.
    Skips the header row (row 0 is assumed to be a header if it's non-numeric).
    """
    values = []
    for row_idx in range(len(rows)):
        if row_idx >= stop_row:
            break
        if col_idx >= len(rows[row_idx]):
            continue
        cell = str(rows[row_idx][col_idx]).strip()
        # Strip common currency/formatting characters
        cell_clean = re.sub(r"[$,£€¥%\s]", "", cell)
        try:
            values.append(float(cell_clean))
        except ValueError:
            continue
    return values


def _compute(op: str, values: List[float]) -> str:
    """Compute the result for the given operation and format it as a string."""
    if not values:
        return "0"
    if op == "sum":
        result = sum(values)
    elif op == "average":
        result = sum(values) / len(values)
    elif op == "count":
        return str(len(values))
    elif op == "min":
        result = min(values)
    elif op == "max":
        result = max(values)
    else:
        return "0"

    # Format: drop trailing zeros for clean presentation
    if result == int(result):
        return str(int(result))
    return f"{result:.2f}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preprocess_rows(rows: List[List[str]]) -> List[List[str]]:
    """
    Scan rows for trigger labels and replace them with computed values.

    The input rows list is NOT mutated — a new list is returned.
    Only the last row is checked for triggers (summary rows are almost
    always at the bottom). This keeps processing O(n) and avoids
    accidentally replacing a label mid-table.

    Args:
        rows: List of rows, each row being a list of cell strings.
              This is the same format type_spreadsheet() expects.

    Returns:
        A new rows list with trigger labels replaced by computed values.

    Example:
        Input rows:
            [["Name", "Price", "Qty"],
             ["Widget A", "10.00", "3"],
             ["Widget B", "25.50", "1"],
             ["", "total", "count"]]

        Output rows:
            [["Name", "Price", "Qty"],
             ["Widget A", "10.00", "3"],
             ["Widget B", "25.50", "1"],
             ["", "35.50", "2"]]
    """
    if not rows or len(rows) < 2:
        return rows

    # Work on a shallow copy of rows; deep-copy only the last row if needed
    result = list(rows)
    last_row_idx = len(rows) - 1
    last_row = list(rows[last_row_idx])  # copy so we can mutate it
    modified = False

    for col_idx, cell in enumerate(last_row):
        op = _is_trigger(cell)
        if op is None:
            continue

        values = _collect_numeric_column(rows, col_idx, stop_row=last_row_idx)
        computed = _compute(op, values)
        logger.debug(
            "auto_calculations: col=%d trigger=%r op=%r values=%s -> %r",
            col_idx, cell, op, values, computed,
        )
        last_row[col_idx] = computed
        modified = True

    if modified:
        result[last_row_idx] = last_row
        logger.info(
            "auto_calculations: substituted %d trigger(s) in last row",
            sum(1 for c in last_row if c != rows[last_row_idx][last_row.index(c)]
                if last_row.index(c) < len(rows[last_row_idx])),
        )

    return result

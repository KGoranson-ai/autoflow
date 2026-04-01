"""
Retry sessions and batch history for Smart Fill.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from smart_fill import SmartFillSession


class RetryManager:
    """Load error logs and build retry sessions."""

    def load_error_log(self, filepath: str) -> List[Dict[str, Any]]:
        errors: List[Dict[str, Any]] = []
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                errors.append(
                    {
                        "row_number": int(row["row_number"]),
                        "data": json.loads(row["data"]),
                        "error_type": row["error_type"],
                        "timestamp": row["timestamp"],
                    }
                )
        return errors

    def create_retry_session(
        self, errors: List[Dict[str, Any]], original_mapping: Dict[str, Any]
    ) -> SmartFillSession:
        retry_data = pd.DataFrame([entry["data"] for entry in errors])
        session = SmartFillSession()
        session.csv_data = retry_data
        session.column_headers = retry_data.columns.tolist()
        session.field_mappings = original_mapping.get("fields", [])
        auto_cfg = original_mapping.get("auto_advance", {})
        for key, value in auto_cfg.items():
            if hasattr(session.auto_advance, key):
                setattr(session.auto_advance, key, value)
        session.current_row = 0
        return session

    def get_recent_batches(self, limit: int = 5) -> List[Dict[str, Any]]:
        history_file = os.path.expanduser("~/Documents/Typestra/History/batches.json")
        if not os.path.exists(history_file):
            return []
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        batches = history.get("batches", [])
        return sorted(batches, key=lambda b: b.get("timestamp", ""), reverse=True)[:limit]


class BatchHistory:
    """Track Smart Fill batch metadata."""

    def __init__(self) -> None:
        self.history_file = os.path.expanduser("~/Documents/Typestra/History/batches.json")

    def save_batch_metadata(
        self,
        batch_id: str,
        csv_file: str,
        total_rows: int,
        successful: int,
        errors: int,
        mapping_used: str,
    ) -> None:
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        if os.path.exists(self.history_file):
            with open(self.history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        else:
            history = {"batches": []}

        history["batches"].append(
            {
                "id": batch_id,
                "csv_file": csv_file,
                "timestamp": datetime.now().isoformat(),
                "total_rows": total_rows,
                "successful": successful,
                "errors": errors,
                "error_log": f"Errors/{batch_id}.csv" if errors > 0 else None,
                "mapping_used": mapping_used,
            }
        )

        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

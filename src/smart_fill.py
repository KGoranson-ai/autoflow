"""
Smart Fill core orchestration for bulk form automation.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import random
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from browser_context import BrowserContext
from error_detection import ErrorLogger


FieldConfig = Dict[str, Any]
logger = logging.getLogger(__name__)


def _sf_debug(message: str) -> None:
    """Write verbose Smart Fill debug logs."""
    logger.debug(message)


class CSVImporter:
    """CSV parser with validation and fallback encodings."""

    @staticmethod
    def parse_csv(filepath: str) -> Dict[str, Any]:
        encodings = ("utf-8", "latin-1")
        last_error = None

        for encoding in encodings:
            try:
                df = pd.read_csv(filepath, encoding=encoding)
                if df.empty and len(df.columns) == 0:
                    return {"success": False, "error": "CSV is empty."}
                if any(not str(col).strip() for col in df.columns):
                    return {"success": False, "error": "CSV contains missing header names."}
                if len(set(df.columns.tolist())) != len(df.columns):
                    return {"success": False, "error": "CSV contains duplicate headers."}
                return {
                    "success": True,
                    "data": df,
                    "preview": df.head(5).fillna("").to_dict("records"),
                    "row_count": len(df),
                    "column_count": len(df.columns),
                    "encoding": encoding,
                }
            except Exception as exc:  # pragma: no cover - defensive
                last_error = exc

        return {"success": False, "error": f"Failed to parse CSV: {last_error}"}


@dataclass
class AutoAdvanceController:
    """Delay and progression policy."""

    enabled: bool = True
    delay_seconds: int = 3
    action: str = "next_row"  # next_row | submit_form
    timeout_seconds: int = 10
    stop_on_error: bool = False
    navigation: str = "tab"  # tab | enter


class FieldMapper:
    """Column-to-form field mapping container."""

    def __init__(self) -> None:
        self.field_mappings: List[Optional[FieldConfig]] = []

    def map_field(self, position: int, field_config: FieldConfig) -> None:
        while len(self.field_mappings) < position:
            self.field_mappings.append(None)
        self.field_mappings[position - 1] = field_config

    def list(self) -> List[Optional[FieldConfig]]:
        return self.field_mappings


class SmartFillSession:
    """Manages Smart Fill workflow: data, mappings, and batch execution."""

    def __init__(self) -> None:
        self.csv_data: Optional[pd.DataFrame] = None
        self.csv_filename: str = ""
        self.csv_filepath: str = ""
        self.column_headers: List[str] = []
        self.mapper = FieldMapper()
        self.current_row = 0
        self.auto_advance = AutoAdvanceController()
        self.is_paused = False
        self.is_running = False
        self.preflight_active = False
        self.batch_id: Optional[str] = None
        self.browser_context: Optional[Dict[str, Any]] = None
        self.error_count = 0
        self.success_count = 0
        self._manual_advance_event = threading.Event()

    @property
    def field_mappings(self) -> List[Optional[FieldConfig]]:
        return self.mapper.list()

    @field_mappings.setter
    def field_mappings(self, value: List[Optional[FieldConfig]]) -> None:
        self.mapper.field_mappings = value

    @property
    def auto_advance_config(self) -> Dict[str, Any]:
        return dict(self.auto_advance.__dict__)

    @auto_advance_config.setter
    def auto_advance_config(self, value: Dict[str, Any]) -> None:
        for key, setting in value.items():
            if hasattr(self.auto_advance, key):
                setattr(self.auto_advance, key, setting)

    def load_csv(self, filepath: str) -> Dict[str, Any]:
        result = CSVImporter.parse_csv(filepath)
        if result.get("success"):
            self.csv_data = result["data"]
            self.column_headers = self.csv_data.columns.tolist()
            self.csv_filename = os.path.basename(filepath)
            self.csv_filepath = os.path.abspath(filepath)
        return result

    def get_demo_csv_path(self, demo_type: str) -> str:
        filename_map = {
            "candidates": "demo_candidates.csv",
            "crm": "demo_crm_contacts.csv",
            "invoices": "demo_invoices.csv",
        }
        filename = filename_map.get(demo_type, "demo_candidates.csv")
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(repo_root, "resources", "demo_data", filename)

    def load_demo_csv(self, demo_type: str = "candidates") -> Dict[str, Any]:
        return self.load_csv(self.get_demo_csv_path(demo_type))

    def map_field(self, position: int, field_config: FieldConfig) -> None:
        self.mapper.map_field(position, field_config)

    def get_value_for_field(self, position: int) -> Optional[str]:
        if self.csv_data is None or self.current_row >= len(self.csv_data):
            return None
        if position > len(self.field_mappings):
            return None

        field_config = self.field_mappings[position - 1]
        if not field_config:
            return None

        field_type = field_config.get("type", "text")
        if field_type != "text":
            return None

        column = field_config.get("column")
        if not column or column not in self.csv_data.columns:
            return None

        value = self.csv_data.loc[self.current_row, column]
        if pd.isna(value) or str(value).strip() == "":
            return None if field_config.get("skip_empty", False) else ""

        cleaned = str(value).replace("\r", " ").replace("\n", " ").strip()
        return cleaned

    def execute_batch(
        self,
        typing_engine: Any,
        error_detector: Any,
        checkpoint_manager: Any,
        *,
        status_cb: Optional[Callable[[str], None]] = None,
        row_cb: Optional[Callable[[int], None]] = None,
        browser_cb: Optional[Callable[[str], None]] = None,
        completion_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        if self.csv_data is None:
            raise ValueError("No CSV loaded.")

        # Force unpaused at thread entry to avoid races during pre-batch focus delay.
        self.is_paused = False
        try:
            self.preflight_active = True
            detected_browser_type = self._prepare_browser_focus_before_batch()
            self.preflight_active = False
            _sf_debug(f"POST-FOCUS is_paused={self.is_paused}")
            if browser_cb:
                browser_cb(detected_browser_type)
            _sf_debug(
                "execute_batch start: "
                f"row={self.current_row}, total_rows={len(self.csv_data)}, "
                f"auto_advance_enabled={self.auto_advance.enabled}, "
                f"timeout_seconds={self.auto_advance.timeout_seconds}, "
                f"detected_browser_type={detected_browser_type!r}"
            )
            self.is_running = True
            self.batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.error_count = 0
            self.success_count = 0
            self.browser_context = BrowserContext().capture_context()
            _sf_debug(f"Captured initial browser context: {self.browser_context}")
            _sf_debug("About to save_state_to_disk")
            self.save_state_to_disk()
            _sf_debug("save_state_to_disk complete")
            _sf_debug(
                f"Entering main batch loop: current_row={self.current_row}, total_rows={len(self.csv_data)}"
            )
            _sf_debug(
                f"PRE-LOOP STATE: is_paused={self.is_paused}, is_running={self.is_running}"
            )

            while self.current_row < len(self.csv_data) and self.is_running:
                _sf_debug(
                    f"Loop top: current_row={self.current_row}, is_running={self.is_running}, is_paused={self.is_paused}"
                )
                while self.is_paused and self.is_running:
                    time.sleep(0.1)
                if not self.is_running:
                    break

                if row_cb:
                    row_cb(self.current_row)

                row_had_typing_exception = False
                _sf_debug(f"Starting fill_current_row for row_index={self.current_row}")
                try:
                    self.fill_current_row(typing_engine)
                except Exception as exc:
                    row_had_typing_exception = True
                    self.error_count += 1
                    self.log_error(self.current_row, f"typing_exception:{type(exc).__name__}")
                    _sf_debug(
                        f"Typing exception in row_index={self.current_row}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    _sf_debug(traceback.format_exc())
                    if self.auto_advance.stop_on_error:
                        self.save_state_to_disk()
                        self.stop()
                        break
                _sf_debug(f"Finished fill_current_row for row_index={self.current_row}")

                if not row_had_typing_exception:
                    # Success is based on typing completion; timeout detector is advisory only.
                    self.success_count += 1
                    _sf_debug(
                        f"Row {self.current_row} counted as success "
                        "(fill_current_row completed without typing exception)"
                    )

                _sf_debug(
                    "Running error detector (advisory only): "
                    f"max_wait={self.auto_advance.timeout_seconds}, row_index={self.current_row}"
                )
                error_result = error_detector.detect_error(
                    max_wait=self.auto_advance.timeout_seconds
                )
                _sf_debug(
                    f"Error detector result for row_index={self.current_row}: {error_result}"
                )
                if error_result == "timeout_error":
                    self.log_error(self.current_row, error_result)

                row_number = self.current_row + 1
                self.auto_save_state_periodically()
                if checkpoint_manager.should_pause_for_checkpoint(row_number):
                    self.pause_for_checkpoint(checkpoint_manager, status_cb=status_cb)

                if self.auto_advance.enabled:
                    self.auto_advance_to_next_row(typing_engine, status_cb=status_cb)
                else:
                    if status_cb:
                        status_cb("Manual mode: waiting for Next Row command...")
                    self.wait_for_manual_advance()
                if row_cb and self.is_running:
                    row_cb(self.current_row)

        except Exception as exc:
            _sf_debug(f"FATAL execute_batch exception: {type(exc).__name__}: {exc}")
            _sf_debug(traceback.format_exc())
        finally:
            self.preflight_active = False
            self.on_batch_complete(completion_cb=completion_cb)

    def _prepare_browser_focus_before_batch(self) -> str:
        """
        Give the user a moment to focus the browser, then optionally
        activate Chrome if a browser is not frontmost, and re-detect.
        """
        if platform.system() != "Darwin":
            _sf_debug("Skipping browser focus preparation (non-macOS)")
            return "other"

        _sf_debug("Pre-batch focus handoff: waiting 2.5s for user to click browser")
        time.sleep(2.5)

        context = BrowserContext()
        frontmost_before = context.get_frontmost_app()
        _sf_debug(f"Frontmost app after delay: {frontmost_before!r}")

        browser_apps = {"Safari", "Google Chrome", "Brave Browser", "Firefox"}
        if frontmost_before not in browser_apps:
            _sf_debug(
                "Frontmost app is not a supported browser. Attempting AppleScript activation for Google Chrome."
            )
            script = 'tell application "Google Chrome" to activate'
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
            _sf_debug(
                "Chrome activate AppleScript result: "
                f"returncode={result.returncode}, stdout={result.stdout.strip()!r}, "
                f"stderr={result.stderr.strip()!r}"
            )
            time.sleep(1.0)

        frontmost_after = context.get_frontmost_app()
        browser_type_after = context.get_browser_type()
        _sf_debug(
            "Frontmost app re-detected before batch typing: "
            f"app={frontmost_after!r}, browser_type={browser_type_after!r}"
        )
        return browser_type_after

    def fill_current_row(self, typing_engine: Any) -> None:
        _sf_debug(f"ENTER fill_current_row row_index={self.current_row}")
        _sf_debug(
            f"fill_current_row enter: row_index={self.current_row}, "
            f"mapped_fields={len(self.field_mappings)}"
        )
        for position, field_config in enumerate(self.field_mappings, start=1):
            _sf_debug(f"Field position={position} config={field_config}")
            if not field_config:
                _sf_debug(f"Field position={position} skipped (no mapping)")
                continue
            if field_config.get("type", "text") != "text":
                _sf_debug(
                    f"Field position={position} skipped (type={field_config.get('type')})"
                )
                continue

            value = self.get_value_for_field(position)
            if value is None:
                _sf_debug(
                    f"Field position={position} value=None -> press_tab()"
                )
                typing_engine.press_tab()
                continue

            preview = value if len(value) <= 80 else value[:77] + "..."
            _sf_debug(
                f"Field position={position} typing value len={len(value)} preview={preview!r}"
            )
            typing_engine.type_text(value)
            time.sleep(random.uniform(0.1, 0.3))

            if self.auto_advance.navigation == "enter":
                _sf_debug(
                    f"Field position={position} navigation=enter -> press_enter()"
                )
                typing_engine.press_enter()
            else:
                _sf_debug(
                    f"Field position={position} navigation=tab -> press_tab()"
                )
                typing_engine.press_tab()
        _sf_debug(f"fill_current_row exit: row_index={self.current_row}")

    def auto_advance_to_next_row(
        self, typing_engine: Any, *, status_cb: Optional[Callable[[str], None]] = None
    ) -> None:
        delay = max(1, int(self.auto_advance.delay_seconds))
        for i in range(delay, 0, -1):
            if status_cb:
                status_cb(f"Next row in {i} seconds...")
            time.sleep(1)
            if not self.is_running:
                return

        if self.auto_advance.action == "submit_form":
            typing_engine.press_enter()
            time.sleep(1)

        self.current_row += 1

    def pause_for_checkpoint(
        self, checkpoint_manager: Any, *, status_cb: Optional[Callable[[str], None]] = None
    ) -> None:
        self.is_paused = True
        checkpoint_manager.show_checkpoint_notification(self.current_row + 1)
        for i in range(checkpoint_manager.pause_duration, 0, -1):
            if status_cb:
                status_cb(f"CHECKPOINT: verify submission ({i}s)")
            time.sleep(1)
            if not self.is_paused or not self.is_running:
                break
        self.is_paused = False

    def wait_for_manual_advance(self) -> None:
        self._manual_advance_event.clear()
        while self.is_running and not self._manual_advance_event.is_set():
            time.sleep(0.1)

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def stop(self) -> None:
        self.save_state_to_disk()
        self.is_running = False
        self._manual_advance_event.set()

    def next_row_manual(self) -> None:
        if self.csv_data is None:
            self.current_row += 1
            self._manual_advance_event.set()
            return
        total_rows = len(self.csv_data)
        # Clamp to total_rows so progress cannot exceed 100%.
        self.current_row = min(self.current_row + 1, total_rows)
        self._manual_advance_event.set()

    def reset(self) -> None:
        self.current_row = 0

    def save_mapping(self, template_name: str) -> str:
        mapping_data = {
            "name": template_name,
            "version": 2,
            "fields": self.field_mappings,
            "navigation": self.auto_advance.navigation,
            "auto_advance": self.auto_advance.__dict__,
            "created_at": datetime.now().isoformat(),
        }
        filepath = os.path.expanduser(
            f"~/Documents/Typestra/Mappings/{template_name}.json"
        )
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, indent=2)
        return filepath

    def load_mapping(self, template_name: str) -> Dict[str, Any]:
        filepath = os.path.expanduser(
            f"~/Documents/Typestra/Mappings/{template_name}.json"
        )
        with open(filepath, "r", encoding="utf-8") as f:
            mapping_data = json.load(f)

        self.field_mappings = mapping_data.get("fields", [])
        auto_cfg = mapping_data.get("auto_advance", {})
        for key, value in auto_cfg.items():
            if hasattr(self.auto_advance, key):
                setattr(self.auto_advance, key, value)
        return mapping_data

    def log_error(self, row_num: int, error_type: str) -> None:
        if self.csv_data is None or self.batch_id is None:
            return
        logger = ErrorLogger(self.batch_id)
        logger.log_error(
            row_number=row_num,
            data=self.csv_data.loc[row_num].to_dict(),
            error_type=error_type,
            notes="",
        )

    def on_batch_complete(
        self, *, completion_cb: Optional[Callable[[int, int], None]] = None
    ) -> None:
        self.is_running = False
        self.is_paused = False
        self.clear_recovery_state()
        if completion_cb:
            completion_cb(self.success_count, self.error_count)

    def _recovery_file_path(self) -> str:
        return os.path.expanduser("~/Documents/Typestra/Recovery/session_state.json")

    def save_state_to_disk(self) -> str:
        total_rows = len(self.csv_data) if self.csv_data is not None else 0
        csv_path = None
        if self.csv_filepath:
            csv_path = os.path.abspath(self.csv_filepath)
        elif self.csv_filename:
            csv_path = os.path.abspath(self.csv_filename)
        state = {
            "batch_id": self.batch_id,
            "csv_file": csv_path,
            "current_row": int(self.current_row),
            "total_rows": int(total_rows),
            "field_mappings": self.field_mappings,
            "auto_advance_config": self.auto_advance_config,
            "browser_context": self.browser_context,
            "timestamp": datetime.now().isoformat(),
            "status": "interrupted",
        }
        recovery_file = self._recovery_file_path()
        os.makedirs(os.path.dirname(recovery_file), exist_ok=True)
        with open(recovery_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        return recovery_file

    def auto_save_state_periodically(self) -> None:
        self.save_state_to_disk()

    def clear_recovery_state(self) -> None:
        path = self._recovery_file_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

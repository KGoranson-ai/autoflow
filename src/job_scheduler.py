"""
Job Scheduler — Schedule a CSV batch to run at a future date/time.

Jobs are persisted to ~/Documents/Typestra/scheduled_jobs.json so they
survive app restarts. A background thread wakes every 30 seconds and
fires any pending job whose scheduled_at time has passed.

Typical flow:
    1. User loads a CSV in the UI and picks a run time.
    2. UI calls scheduler.add_job(csv_path, scheduled_at, rows).
    3. The background thread fires the job when the time arrives.
    4. Job status transitions: pending -> running -> done (or failed).
    5. UI polls scheduler.list_jobs() to show the queue.

Integration with TypingEngine:
    Pass a factory callable as the `engine_factory` argument so the
    scheduler can create a fresh TypingEngine for each job without
    importing it directly (avoids circular imports).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job status constants
# ---------------------------------------------------------------------------
STATUS_PENDING  = "pending"
STATUS_RUNNING  = "running"
STATUS_DONE     = "done"
STATUS_FAILED   = "failed"
STATUS_CANCELLED = "cancelled"

# How often the background thread wakes to check for due jobs (seconds)
_POLL_INTERVAL = 30


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _default_jobs_path() -> str:
    return os.path.expanduser("~/Documents/Typestra/scheduled_jobs.json")


def _load_jobs(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load jobs file %r: %s", path, exc)
        return []


def _save_jobs(jobs: List[Dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.error("Could not save jobs file %r: %s", path, exc)


# ---------------------------------------------------------------------------
# Job dict helpers
# ---------------------------------------------------------------------------

def _make_job(
    csv_path: str,
    scheduled_at: datetime,
    rows: Optional[List[List[str]]] = None,
    label: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new job dict."""
    return {
        "job_id":       str(uuid.uuid4()),
        "csv_path":     csv_path,
        "scheduled_at": scheduled_at.isoformat(),
        "label":        label or os.path.basename(csv_path),
        "rows":         rows,           # in-memory only; not written to disk
        "status":       STATUS_PENDING,
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "started_at":   None,
        "finished_at":  None,
        "error":        None,
    }


def _is_due(job: Dict[str, Any]) -> bool:
    """Return True if the job is pending and its scheduled time has passed."""
    if job["status"] != STATUS_PENDING:
        return False
    try:
        scheduled = datetime.fromisoformat(job["scheduled_at"])
        # Make timezone-aware if naive (assume local time)
        if scheduled.tzinfo is None:
            scheduled = scheduled.astimezone()
        return datetime.now(timezone.utc) >= scheduled.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class JobScheduler:
    """
    Manages a queue of scheduled CSV typing jobs.

    Args:
        engine_factory: Callable that accepts (rows, on_status) and returns
                        a started typing session. Signature:
                            engine_factory(rows: List[List[str]],
                                           on_status: Callable[[str], None]) -> None
                        The factory is responsible for running the job
                        (blocking until complete).
        jobs_path:      Path to the JSON persistence file. Defaults to
                        ~/Documents/Typestra/scheduled_jobs.json.
    """

    def __init__(
        self,
        engine_factory: Optional[Callable] = None,
        jobs_path: Optional[str] = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._jobs_path = jobs_path or _default_jobs_path()
        self._lock = threading.Lock()
        self._jobs: List[Dict[str, Any]] = _load_jobs(self._jobs_path)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._status_callbacks: List[Callable[[str, str], None]] = []

    # ------------------------------------------------------------------
    # Job management
    # ------------------------------------------------------------------

    def add_job(
        self,
        csv_path: str,
        scheduled_at: datetime,
        rows: Optional[List[List[str]]] = None,
        label: Optional[str] = None,
    ) -> str:
        """
        Schedule a CSV batch to run at scheduled_at.

        Args:
            csv_path:     Path to the source CSV file (stored for reference).
            scheduled_at: When to run the job (naive datetime = local time).
            rows:         Pre-loaded rows [[cell, ...], ...]. If None, the
                          scheduler reads the CSV at run time.
            label:        Human-readable name shown in the queue UI.

        Returns:
            job_id string.
        """
        job = _make_job(csv_path, scheduled_at, rows=rows, label=label)
        with self._lock:
            self._jobs.append(job)
            self._persist()
        logger.info(
            "Scheduled job %s (%r) for %s",
            job["job_id"], job["label"], job["scheduled_at"],
        )
        return job["job_id"]

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a pending job. Returns True if cancelled, False if not found
        or already running/done.
        """
        with self._lock:
            for job in self._jobs:
                if job["job_id"] == job_id and job["status"] == STATUS_PENDING:
                    job["status"] = STATUS_CANCELLED
                    self._persist()
                    logger.info("Cancelled job %s", job_id)
                    return True
        return False

    def list_jobs(self, include_done: bool = True) -> List[Dict[str, Any]]:
        """
        Return a list of job dicts sorted by scheduled_at.
        Pass include_done=False to show only pending/running jobs.
        """
        with self._lock:
            jobs = list(self._jobs)

        if not include_done:
            jobs = [j for j in jobs if j["status"] in (STATUS_PENDING, STATUS_RUNNING)]

        return sorted(jobs, key=lambda j: j["scheduled_at"])

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the job dict for a given job_id, or None."""
        with self._lock:
            for job in self._jobs:
                if job["job_id"] == job_id:
                    return dict(job)
        return None

    def clear_done_jobs(self) -> int:
        """Remove all done/failed/cancelled jobs. Returns count removed."""
        with self._lock:
            before = len(self._jobs)
            self._jobs = [
                j for j in self._jobs
                if j["status"] not in (STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED)
            ]
            removed = before - len(self._jobs)
            if removed:
                self._persist()
        return removed

    # ------------------------------------------------------------------
    # Status change notifications
    # ------------------------------------------------------------------

    def on_status_change(self, callback: Callable[[str, str], None]) -> None:
        """
        Register a callback to receive job status changes.
        Signature: callback(job_id: str, new_status: str)
        """
        self._status_callbacks.append(callback)

    def _notify(self, job_id: str, status: str) -> None:
        for cb in self._status_callbacks:
            try:
                cb(job_id, status)
            except Exception as exc:
                logger.warning("Status callback error: %s", exc)

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background polling thread. Safe to call multiple times."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("JobScheduler started (polling every %ds)", _POLL_INTERVAL)

    def stop(self) -> None:
        """Stop the background thread gracefully."""
        self._running = False
        logger.info("JobScheduler stopped")

    def _poll_loop(self) -> None:
        while self._running:
            self._check_due_jobs()
            time.sleep(_POLL_INTERVAL)

    def _check_due_jobs(self) -> None:
        with self._lock:
            due = [j for j in self._jobs if _is_due(j)]

        for job in due:
            self._fire_job(job)

    def _fire_job(self, job: Dict[str, Any]) -> None:
        """Run a due job in its own thread so the poller isn't blocked."""
        def _run():
            job_id = job["job_id"]
            logger.info("Firing job %s (%r)", job_id, job["label"])

            with self._lock:
                job["status"] = STATUS_RUNNING
                job["started_at"] = datetime.now(timezone.utc).isoformat()
                self._persist()
            self._notify(job_id, STATUS_RUNNING)

            try:
                rows = job.get("rows")
                if rows is None:
                    rows = self._load_rows_from_csv(job["csv_path"])

                if self._engine_factory is None:
                    raise RuntimeError(
                        "No engine_factory set on JobScheduler. "
                        "Pass engine_factory when constructing JobScheduler."
                    )

                def on_status(msg: str) -> None:
                    logger.info("[job %s] %s", job_id, msg)

                self._engine_factory(rows, on_status)

                with self._lock:
                    job["status"] = STATUS_DONE
                    job["finished_at"] = datetime.now(timezone.utc).isoformat()
                    self._persist()
                self._notify(job_id, STATUS_DONE)
                logger.info("Job %s completed successfully", job_id)

            except Exception as exc:
                logger.error("Job %s failed: %s", job_id, exc, exc_info=True)
                with self._lock:
                    job["status"] = STATUS_FAILED
                    job["finished_at"] = datetime.now(timezone.utc).isoformat()
                    job["error"] = str(exc)
                    self._persist()
                self._notify(job_id, STATUS_FAILED)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_rows_from_csv(csv_path: str) -> List[List[str]]:
        """Load rows from a CSV file at run time (if rows weren't pre-loaded)."""
        import csv
        path = os.path.expanduser(csv_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"CSV file not found at run time: {path!r}")
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            return [row for row in reader]

    def _persist(self) -> None:
        """Write jobs to disk. Must be called with self._lock held."""
        # Strip in-memory-only 'rows' field before writing
        serializable = []
        for job in self._jobs:
            j = dict(job)
            j.pop("rows", None)
            serializable.append(j)
        _save_jobs(serializable, self._jobs_path)


# ---------------------------------------------------------------------------
# Convenience: parse a user-supplied datetime string
# ---------------------------------------------------------------------------

def parse_schedule_time(value: str) -> datetime:
    """
    Parse a schedule time string entered by the user.
    Accepts several common formats:
        "2026-04-17 18:00"
        "2026-04-17 18:00:00"
        "04/17/2026 6:00 PM"
        "tomorrow 9am"   (not supported — raise ValueError with hint)

    Returns a timezone-aware datetime in local time.
    Raises ValueError if the string cannot be parsed.
    """
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M%p",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]
    value = value.strip()
    for fmt in formats:
        try:
            naive = datetime.strptime(value, fmt)
            return naive.astimezone()  # Convert to local timezone-aware
        except ValueError:
            continue

    raise ValueError(
        f"Could not parse schedule time: {value!r}\n"
        "Expected format: YYYY-MM-DD HH:MM  (e.g. '2026-04-17 18:00')"
    )

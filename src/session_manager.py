"""
SessionManager — orchestrates parallel TypingEngine sessions for multi-form mode.

Each session targets one browser tab via a TabInjector emit backend.
Sessions run in independent threads — one can pause, fail, or finish
without affecting the others.

License gate: Team only. Raises FeatureNotAvailableError for
lower tiers.

Usage:
    manager = SessionManager(license_info=my_license)
    manager.add_session(tab_index=1, browser_type="chrome", rows=rows)
    manager.add_session(tab_index=2, browser_type="chrome", rows=rows)
    manager.start_all()
    # Poll manager.get_status() to update UI
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session status constants
# ---------------------------------------------------------------------------
STATUS_IDLE      = "idle"
STATUS_RUNNING   = "running"
STATUS_PAUSED    = "paused"
STATUS_DONE      = "done"
STATUS_FAILED    = "failed"
STATUS_STOPPED   = "stopped"


class FeatureNotAvailableError(Exception):
    """Raised when multi-form is accessed on a non-qualifying tier."""


def _require_team(license_info) -> None:
    if license_info is None:
        return
    tier = getattr(license_info, "tier", "solo")
    if tier != "team":
        raise FeatureNotAvailableError(
            "Multi-Form Fill requires a Team license. "
            "Upgrade at typestra.com to unlock this feature."
        )


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class Session:
    """Represents one tab assignment and its typing engine."""
    session_id:   int
    tab_index:    int
    browser_type: str
    rows:         List[List[str]]

    # Runtime state
    status:       str = STATUS_IDLE
    progress:     int = 0          # 0-100
    status_msg:   str = ""
    error:        Optional[str] = None
    thread:       Optional[threading.Thread] = field(default=None, repr=False)

    # Control flags (read by the engine via callbacks)
    _stop_flag:   bool = field(default=False, repr=False)
    _pause_flag:  bool = field(default=False, repr=False)
    _lock:        threading.Lock = field(default_factory=threading.Lock, repr=False)

    def should_stop(self) -> bool:
        with self._lock:
            return self._stop_flag

    def is_paused(self) -> bool:
        with self._lock:
            return self._pause_flag

    def on_status(self, msg: str) -> None:
        self.status_msg = msg
        # Parse progress percentage out of engine status messages like
        # "⌨️ Typing... 43% complete" or "📊 Filling spreadsheet... 43%"
        import re
        m = re.search(r"(\d+)%", msg)
        if m:
            self.progress = int(m.group(1))


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """
    Manages multiple parallel TypingEngine sessions, one per browser tab.

    Args:
        license_info: LicenseInfo object from LicenseManager. Used to gate
                      the feature to Team tier.
        config:       TypingConfig shared across all sessions. If None, a
                      default config is used.
        on_session_update: Optional callback(session_id, session) fired
                      whenever a session's status or progress changes.
    """

    def __init__(
        self,
        license_info=None,
        config=None,
        on_session_update: Optional[Callable[[int, Session], None]] = None,
    ) -> None:
        _require_team(license_info)
        self._license_info = license_info
        self._config = config
        self._on_session_update = on_session_update
        self._sessions: List[Session] = []
        self._lock = threading.Lock()
        self._next_id = 1

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def add_session(
        self,
        tab_index: int,
        browser_type: str,
        rows: List[List[str]],
    ) -> int:
        """
        Register a tab assignment.

        Args:
            tab_index:    1-based tab number in the front browser window.
            browser_type: Canonical browser key: 'chrome', 'brave', 'safari', 'edge'.
            rows:         CSV rows to type into this tab.

        Returns:
            session_id integer.
        """
        session = Session(
            session_id=self._next_id,
            tab_index=tab_index,
            browser_type=browser_type,
            rows=rows,
        )
        with self._lock:
            self._sessions.append(session)
            self._next_id += 1
        logger.info(
            "SessionManager: added session %d -> tab %d (%s), %d rows",
            session.session_id, tab_index, browser_type, len(rows),
        )
        return session.session_id

    def remove_session(self, session_id: int) -> bool:
        """Remove an idle session. Returns False if the session is running."""
        with self._lock:
            for i, s in enumerate(self._sessions):
                if s.session_id == session_id:
                    if s.status == STATUS_RUNNING:
                        return False
                    self._sessions.pop(i)
                    return True
        return False

    def clear_sessions(self) -> None:
        """Remove all non-running sessions."""
        with self._lock:
            self._sessions = [s for s in self._sessions if s.status == STATUS_RUNNING]

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        """Launch all idle sessions in parallel threads."""
        with self._lock:
            idle = [s for s in self._sessions if s.status == STATUS_IDLE]

        if not idle:
            logger.info("SessionManager.start_all: no idle sessions to start")
            return

        logger.info("SessionManager: starting %d session(s)", len(idle))
        for session in idle:
            t = threading.Thread(
                target=self._run_session,
                args=(session,),
                daemon=True,
                name=f"typestra-session-{session.session_id}",
            )
            session.thread = t
            t.start()

    def start_session(self, session_id: int) -> bool:
        """Start a single idle session. Returns False if not found or not idle."""
        session = self._get_session(session_id)
        if session is None or session.status != STATUS_IDLE:
            return False
        t = threading.Thread(
            target=self._run_session,
            args=(session,),
            daemon=True,
            name=f"typestra-session-{session_id}",
        )
        session.thread = t
        t.start()
        return True

    def pause_session(self, session_id: int) -> bool:
        session = self._get_session(session_id)
        if session is None or session.status != STATUS_RUNNING:
            return False
        with session._lock:
            session._pause_flag = True
        session.status = STATUS_PAUSED
        self._notify(session)
        logger.info("SessionManager: paused session %d", session_id)
        return True

    def resume_session(self, session_id: int) -> bool:
        session = self._get_session(session_id)
        if session is None or session.status != STATUS_PAUSED:
            return False
        with session._lock:
            session._pause_flag = False
        session.status = STATUS_RUNNING
        self._notify(session)
        logger.info("SessionManager: resumed session %d", session_id)
        return True

    def stop_session(self, session_id: int) -> bool:
        session = self._get_session(session_id)
        if session is None or session.status not in (STATUS_RUNNING, STATUS_PAUSED):
            return False
        with session._lock:
            session._stop_flag = True
            session._pause_flag = False  # Unblock paused engine so it can exit
        logger.info("SessionManager: stop requested for session %d", session_id)
        return True

    def stop_all(self) -> None:
        """Request stop on all running/paused sessions."""
        with self._lock:
            active = [
                s for s in self._sessions
                if s.status in (STATUS_RUNNING, STATUS_PAUSED)
            ]
        for s in active:
            self.stop_session(s.session_id)

    def pause_all(self) -> None:
        with self._lock:
            running = [s for s in self._sessions if s.status == STATUS_RUNNING]
        for s in running:
            self.pause_session(s.session_id)

    def resume_all(self) -> None:
        with self._lock:
            paused = [s for s in self._sessions if s.status == STATUS_PAUSED]
        for s in paused:
            self.resume_session(s.session_id)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> List[Dict[str, Any]]:
        """
        Return a list of status dicts for all sessions, sorted by session_id.
        Safe to call from the UI thread at any time.
        """
        with self._lock:
            sessions = list(self._sessions)

        return [
            {
                "session_id":   s.session_id,
                "tab_index":    s.tab_index,
                "browser_type": s.browser_type,
                "status":       s.status,
                "progress":     s.progress,
                "status_msg":   s.status_msg,
                "error":        s.error,
            }
            for s in sorted(sessions, key=lambda x: x.session_id)
        ]

    @property
    def all_done(self) -> bool:
        """True when every session has reached a terminal state."""
        with self._lock:
            return all(
                s.status in (STATUS_DONE, STATUS_FAILED, STATUS_STOPPED)
                for s in self._sessions
            )

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_session(self, session: Session) -> None:
        """Thread target: run one TypingEngine session."""
        from tab_injector import TabInjector
        from autoflow_engine.typing_engine import TypingEngine, TypingConfig

        session.status = STATUS_RUNNING
        session.progress = 0
        self._notify(session)

        try:
            injector = TabInjector(
                browser_type=session.browser_type,
                tab_index=session.tab_index,
            )

            config = self._config or TypingConfig()

            engine = TypingEngine(
                config,
                should_stop=session.should_stop,
                is_paused=session.is_paused,
                on_status=session.on_status,
                emit_character=injector.emit_character,
                emit_key=injector.emit_key,
            )

            # Run auto_calculations preprocessing
            try:
                from auto_calculations import preprocess_rows
                rows = preprocess_rows(session.rows)
            except ImportError:
                rows = session.rows

            engine.type_spreadsheet(rows)

            if session.should_stop():
                session.status = STATUS_STOPPED
                session.status_msg = "Stopped"
            else:
                session.status = STATUS_DONE
                session.progress = 100
                session.status_msg = "Complete"

        except Exception as exc:
            logger.error(
                "Session %d failed: %s", session.session_id, exc, exc_info=True
            )
            session.status = STATUS_FAILED
            session.error = str(exc)
            session.status_msg = f"Error: {exc}"

        self._notify(session)
        logger.info(
            "Session %d finished with status=%s", session.session_id, session.status
        )

    def _get_session(self, session_id: int) -> Optional[Session]:
        with self._lock:
            for s in self._sessions:
                if s.session_id == session_id:
                    return s
        return None

    def _notify(self, session: Session) -> None:
        if self._on_session_update:
            try:
                self._on_session_update(session.session_id, session)
            except Exception as exc:
                logger.warning("on_session_update callback error: %s", exc)

"""
Sleep/wake detection using system uptime monitoring.
Supports macOS (sysctl kern.boottime) and Windows (GetTickCount64).
"""

from __future__ import annotations

import ctypes
import logging
import platform
import subprocess
import time
from threading import Thread
from typing import Callable, List


logger = logging.getLogger(__name__)

_IS_MAC = platform.system() == "Darwin"
_IS_WIN = platform.system() == "Windows"


class SleepWakeDetector:
    """
    Detect sleep/wake events using system uptime checks.
    Supports macOS and Windows.
    """

    def __init__(self):
        self.last_uptime = self.get_system_uptime()
        self.wake_callbacks: List[Callable[[], None]] = []
        self.monitoring = False

    def register_wake_handler(self, callback: Callable[[], None]):
        self.wake_callbacks.append(callback)
        if not self.monitoring:
            self.start_monitoring()

    def start_monitoring(self):
        """Start background thread to monitor for wake events."""
        self.monitoring = True

        def monitor_loop():
            while self.monitoring:
                current_uptime = self.get_system_uptime()
                # Wake/reboot detected only when uptime unexpectedly moves backward.
                # Use a tolerance to avoid jitter-related false positives.
                if current_uptime < (self.last_uptime - 30):
                    for callback in self.wake_callbacks:
                        try:
                            callback()
                        except Exception as exc:  # pragma: no cover
                            logger.error("Wake callback failed: %s", exc)
                self.last_uptime = current_uptime
                time.sleep(5)

        thread = Thread(target=monitor_loop, daemon=True)
        thread.start()

    def get_system_uptime(self) -> float:
        """
        Get seconds elapsed since last boot.
        - macOS: parsed from sysctl kern.boottime.
        - Windows: ctypes GetTickCount64() (no extra dependency, millisecond precision).
        Returns 0.0 on failure.
        """
        if _IS_WIN:
            try:
                # GetTickCount64 returns milliseconds since boot; no overflow risk
                ms = ctypes.windll.kernel32.GetTickCount64()
                return ms / 1000.0
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to get uptime on Windows: %s", exc)
                return 0.0

        if _IS_MAC:
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "kern.boottime"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = result.stdout.strip()
                sec_str = output.split("sec = ")[1].split(",")[0]
                boot_time = int(sec_str)
                return time.time() - boot_time
            except Exception as exc:  # pragma: no cover
                logger.error("Failed to get uptime on macOS: %s", exc)
                return 0.0

        # Unsupported platform — return current epoch as a stable non-zero value
        # so the sleep detector does not produce spurious wake events.
        return time.time()

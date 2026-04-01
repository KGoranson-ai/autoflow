"""
Sleep/wake detection for macOS using uptime monitoring.
"""

from __future__ import annotations

import logging
import subprocess
import time
from threading import Thread
from typing import Callable, List


logger = logging.getLogger(__name__)


class SleepWakeDetector:
    """
    Detect Mac sleep/wake events using system uptime checks.
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
        Get seconds since boot.
        """
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
            logger.error("Failed to get uptime: %s", exc)
            return 0.0

"""
License Manager — Typestra desktop licensing.

Handles:
- Reading/storing the license key from ~/.autoflow/license.key
- Validating against the Typestra backend API
- Caching validation results locally
- Exposing tier, trial state, and feature flags to the app
"""

from __future__ import annotations

import json
import logging
import os

import requests
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

AUTOFLOW_DIR = os.path.join(os.path.expanduser("~"), ".autoflow")
LICENSE_FILE = os.path.join(AUTOFLOW_DIR, "license.key")
LICENSE_CACHE = os.path.join(AUTOFLOW_DIR, "license_cache.json")
# Production API on Railway (override with TYPESTRA_API_URL for staging / self-hosted).
LICENSE_API = os.environ.get(
    "TYPESTRA_API_URL",
    "https://web-production-028cb.up.railway.app",
)

# Features gated by tier
TIER_FEATURES: dict[str, list[str]] = {
    "solo":  ["text_blocks", "shortcuts", "spreadsheet_mode", "multi_profile"],
    "pro":   ["text_blocks", "shortcuts", "spreadsheet_mode", "multi_profile",
              "ocr", "auto_calculations", "multi_device"],
    "team":  ["text_blocks", "shortcuts", "spreadsheet_mode", "multi_profile",
              "ocr", "auto_calculations", "multi_device",
              "scheduled_scripts", "team_management", "dedicated_support"],
}

# Which features are reserved for paid tiers (vs. trial)
PAID_TIER_FEATURES = {"pro", "team"}


@dataclass
class LicenseInfo:
    valid: bool
    tier: Optional[str]
    expires: Optional[str]
    is_trial: bool
    trial_end: Optional[str]
    days_remaining: int
    features: list[str]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def invalid(error: str = "No license") -> "LicenseInfo":
        return LicenseInfo(
            valid=False,
            tier=None,
            expires=None,
            is_trial=False,
            trial_end=None,
            days_remaining=0,
            features=[],
            error=error,
        )

    @staticmethod
    def from_dict(d: dict) -> "LicenseInfo":
        return LicenseInfo(
            valid=d.get("valid", False),
            tier=d.get("tier"),
            expires=d.get("expires"),
            is_trial=d.get("is_trial", False),
            trial_end=d.get("trial_end"),
            days_remaining=d.get("days_remaining", 0),
            features=d.get("features", []),
            error=d.get("error"),
        )


class LicenseManager:
    # Cache TTL: 5 minutes
    CACHE_TTL_SECONDS = 300

    def __init__(self) -> None:
        self._license_key: Optional[str] = None
        self._cached: Optional[LicenseInfo] = None
        self._cache_time: Optional[datetime] = None

    # ── key storage ──────────────────────────────────────────────────────────

    def get_stored_key(self) -> Optional[str]:
        """Read license key from ~/.autoflow/license.key."""
        if self._license_key:
            return self._license_key
        try:
            os.makedirs(AUTOFLOW_DIR, mode=0o700, exist_ok=True)
            with open(LICENSE_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            self._license_key = key if key else None
        except FileNotFoundError:
            self._license_key = None
        except OSError as e:
            logger.warning("Could not read license file: %s", e)
            self._license_key = None
        return self._license_key

    def store_key(self, key: str) -> None:
        """Persist a license key to ~/.autoflow/license.key."""
        try:
            os.makedirs(AUTOFLOW_DIR, mode=0o700, exist_ok=True)
            tmp = LICENSE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(key.strip())
            os.replace(tmp, LICENSE_FILE)
            self._license_key = key.strip()
            self._invalidate_cache()
        except OSError as e:
            logger.error("Could not write license file: %s", e)

    def clear_key(self) -> None:
        """Remove the stored license key."""
        self._license_key = None
        try:
            os.remove(LICENSE_FILE)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("Could not remove license file: %s", e)
        self._invalidate_cache()

    # ── validation ───────────────────────────────────────────────────────────

    def validate(self, force: bool = False) -> LicenseInfo:
        """
        Validate the stored license key against the backend.

        Uses a local cache (5 min TTL) to avoid hammering the API.
        Set force=True to skip cache and revalidate.
        """
        key = self.get_stored_key()
        if not key:
            return LicenseInfo.invalid("No license key found")

        # Serve from cache if fresh
        if not force and self._is_cache_valid():
            return self._cached

        result = self._fetch_validation(key)
        self._cached = result
        self._cache_time = datetime.now(timezone.utc)
        self._save_cache(result)
        return result

    def validate_and_check_trial(self) -> LicenseInfo:
        """
        Validate license, then check trial expiry client-side.

        Handles the case where a trial key was valid when issued but
        has now expired (server might be unreachable).
        """
        info = self.validate()
        if info.is_trial and info.trial_end:
            try:
                trial_end = datetime.fromisoformat(info.trial_end.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                remaining = (trial_end - now).total_seconds()
                if remaining <= 0:
                    # Trial expired — treat as invalid
                    logger.info("Trial expired (local check), showing upgrade prompt")
                    expired_info = LicenseInfo(
                        valid=False,
                        tier=info.trial_end,
                        expires=info.trial_end,
                        is_trial=True,
                        trial_end=info.trial_end,
                        days_remaining=0,
                        features=[],
                        error="Trial expired",
                    )
                    self._cached = expired_info
                    self._cache_time = datetime.now(timezone.utc)
                    self._save_cache(expired_info)
                    return expired_info
                info.days_remaining = int(remaining // 86400)
            except (ValueError, TypeError):
                pass
        return info

    # ── feature gating ───────────────────────────────────────────────────────

    def has_feature(self, feature: str, license_info: Optional[LicenseInfo] = None) -> bool:
        """Return True if the current tier includes the named feature."""
        info = license_info or self.validate()
        if not info.valid:
            return False
        return feature in info.features

    def is_pro_plus(self, license_info: Optional[LicenseInfo] = None) -> bool:
        """True for Pro or Team tiers."""
        info = license_info or self.validate()
        return info.valid and info.tier in {"pro", "team"}

    def is_team(self, license_info: Optional[LicenseInfo] = None) -> bool:
        """True for Team tier only."""
        info = license_info or self.validate()
        return info.valid and info.tier == "team"

    def requires_upgrade(self, feature: str, license_info: Optional[LicenseInfo] = None) -> bool:
        """Return True if feature is unavailable for the current license."""
        return not self.has_feature(feature, license_info)

    # ── trial helpers ────────────────────────────────────────────────────────

    def is_trial_active(self, license_info: Optional[LicenseInfo] = None) -> bool:
        """True if user is on an active (non-expired) trial."""
        info = license_info or self.validate()
        return info.valid and info.is_trial and info.days_remaining > 0

    def show_trial_banner(self, license_info: Optional[LicenseInfo] = None) -> bool:
        """True if user is valid, on trial, and within the warning window (<= 2 days left)."""
        info = license_info or self.validate()
        return self.is_trial_active(info) and info.days_remaining <= 2

    # ── internal ─────────────────────────────────────────────────────────────

    def _fetch_validation(self, key: str) -> LicenseInfo:
        """POST to /api/validate-license and parse the response."""
        url = f"{LICENSE_API.rstrip('/')}/api/validate-license"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        try:
            response = requests.post(
                url,
                json={"license_key": key},
                headers=headers,
                timeout=10,
            )
            if not response.ok:
                logger.warning(
                    "License validation HTTP %s: %s",
                    response.status_code,
                    response.text[:500],
                )
                return LicenseInfo.invalid(f"Server error {response.status_code}")
            data = response.json()
        except requests.Timeout:
            logger.warning("License validation timed out")
            return LicenseInfo.invalid(
                "Backend timeout - please check your internet connection"
            )
        except requests.ConnectionError:
            logger.warning("License validation connection error")
            return LicenseInfo.invalid(
                "Cannot connect to license server - check internet"
            )
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON from license server: %s", e)
            return LicenseInfo.invalid("Invalid server response")

        # Parse response
        valid = bool(data.get("valid", False))
        tier = data.get("tier")
        expires = data.get("expires")
        is_trial = bool(data.get("is_trial", False))

        # Compute days remaining
        days_remaining = 0
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                delta = exp_dt - datetime.now(timezone.utc)
                days_remaining = max(0, int(delta.total_seconds() / 86400))
            except (ValueError, TypeError):
                days_remaining = 0

        # Build feature list
        features = TIER_FEATURES.get(tier, []) if tier else []

        return LicenseInfo(
            valid=valid,
            tier=tier,
            expires=expires,
            is_trial=is_trial,
            trial_end=expires if is_trial else None,
            days_remaining=days_remaining,
            features=features,
        )

    def _is_cache_valid(self) -> bool:
        if self._cached is None or self._cache_time is None:
            return False
        age = datetime.now(timezone.utc) - self._cache_time
        return age.total_seconds() < self.CACHE_TTL_SECONDS

    def _invalidate_cache(self) -> None:
        self._cached = None
        self._cache_time = None
        try:
            os.remove(LICENSE_CACHE)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _save_cache(self, info: LicenseInfo) -> None:
        try:
            os.makedirs(AUTOFLOW_DIR, mode=0o700, exist_ok=True)
            tmp = LICENSE_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(info.to_dict(), f)
            os.replace(tmp, LICENSE_CACHE)
        except OSError:
            pass

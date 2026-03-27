"""
Typestra license generation, storage, and validation.

Uses models from ``database`` and ``LICENSE_SALT`` for key hashing.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import LicenseValidation, Subscription, User

logger = logging.getLogger(__name__)

# Uppercase alphanumeric, excluding 0, O, 1, I, L (31 symbols → 16 chars via secrets).
_LICENSE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

_VALID_TIERS = frozenset({"basic", "pro", "premium"})

_MAX_KEY_GENERATION_ATTEMPTS = 32

# Placeholder hash when the key cannot be normalized/hashed (audit row still stored).
_PLACEHOLDER_KEY_HASH = "0" * 64


def _normalize_license_key(license_key: str) -> str:
    """Strip, uppercase, remove separators for stable hashing."""
    s = license_key.strip().upper().replace("-", "").replace(" ", "")
    return s


def generate_license_key() -> str:
    """Generate a cryptographically secure 16-character key as XXXX-XXXX-XXXX-XXXX."""
    parts = [
        "".join(secrets.choice(_LICENSE_ALPHABET) for _ in range(4))
        for _ in range(4)
    ]
    return "-".join(parts)


def hash_license_key(license_key: str, salt: str) -> str:
    """SHA-256 hex digest (64 chars) of salt + normalized license key."""
    normalized = _normalize_license_key(license_key)
    if not normalized:
        raise ValueError("license_key is empty after normalization")
    payload = f"{salt}{normalized}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _get_license_salt() -> str:
    return os.environ.get("LICENSE_SALT", "")


def create_subscription(
    session: Session,
    email: str,
    tier: str,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Find or create a user, issue a license, persist subscription as ``active``.

    Caller is responsible for ``session.commit()`` on success.

    Returns:
        ``{"license_key", "tier", "user_id"}`` (``user_id`` as string UUID).
    """
    normalized_email = (email or "").strip().lower()
    if not normalized_email or "@" not in normalized_email:
        raise ValueError("A valid email address is required")

    if tier not in _VALID_TIERS:
        raise ValueError(f"tier must be one of {sorted(_VALID_TIERS)}")

    salt = _get_license_salt()

    user = session.execute(select(User).where(User.email == normalized_email)).scalar_one_or_none()
    if user is None:
        user = User(email=normalized_email)
        session.add(user)
        session.flush()

    license_key: Optional[str] = None
    key_hash: Optional[str] = None

    for _ in range(_MAX_KEY_GENERATION_ATTEMPTS):
        candidate = generate_license_key()
        try:
            digest = hash_license_key(candidate, salt)
        except ValueError:
            continue
        conflict = session.execute(
            select(Subscription.id).where(Subscription.license_key_hash == digest)
        ).scalar_one_or_none()
        if conflict is not None:
            continue
        license_key = candidate
        key_hash = digest
        break

    if license_key is None or key_hash is None:
        raise RuntimeError("Could not generate a unique license key; retry or increase entropy")

    subscription = Subscription(
        user_id=user.id,
        license_key_hash=key_hash,
        tier=tier,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        status="active",
        current_period_end=None,
    )
    try:
        with session.begin_nested():
            session.add(subscription)
            session.flush()
    except IntegrityError as e:
        logger.warning("create_subscription integrity error: %s", e)
        raise RuntimeError("Could not create subscription (duplicate or constraint violation)") from e

    return {
        "license_key": license_key,
        "tier": tier,
        "user_id": str(user.id),
    }


def validate_license(
    session: Session,
    license_key: str,
    salt: str,
    *,
    ip_address: Optional[str] = None,
) -> dict[str, Any]:
    """
    Validate a license key, log the attempt, return outcome.

    Uses the same normalization as ``hash_license_key``. ``salt`` should match
    deployment hashing (typically ``LICENSE_SALT`` from the environment).

    Caller is responsible for ``session.commit()`` so the audit row is persisted.

    Returns:
        ``{"valid": bool, "tier": str | None, "expires": str | None}``
        ``expires`` is ISO 8601 from ``current_period_end``, or ``None``.
    """
    tier: Optional[str] = None
    expires: Optional[str] = None
    valid = False

    try:
        if not license_key or not str(license_key).strip():
            raise ValueError("license_key is required")
        key_hash = hash_license_key(license_key, salt)
    except ValueError as e:
        logger.info("validate_license: invalid key input: %s", e)
        _log_validation(session, _PLACEHOLDER_KEY_HASH, False, ip_address=ip_address)
        return {"valid": False, "tier": None, "expires": None}

    sub = session.execute(
        select(Subscription).where(Subscription.license_key_hash == key_hash)
    ).scalar_one_or_none()

    if sub is not None and sub.status == "active":
        valid = True
        tier = sub.tier
        if sub.current_period_end is not None:
            expires = sub.current_period_end.isoformat()
        else:
            expires = None
    else:
        if sub is not None:
            logger.debug("validate_license: subscription not active (status=%s)", sub.status)

    _log_validation(session, key_hash, valid, ip_address=ip_address)

    return {
        "valid": valid,
        "tier": tier,
        "expires": expires,
    }


def _log_validation(
    session: Session,
    license_key_hash: str,
    valid: bool,
    *,
    ip_address: Optional[str],
) -> None:
    """Append a row to ``license_validations``."""
    row = LicenseValidation(
        license_key_hash=license_key_hash,
        valid=valid,
        ip_address=ip_address,
    )
    session.add(row)
    try:
        session.flush()
    except Exception as e:
        logger.error("Failed to write license_validations row: %s", e)
        raise

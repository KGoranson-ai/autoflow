"""
PostgreSQL schema and ORM for the Typestra license system.

Uses SQLAlchemy 2.x with the psycopg2 driver (postgresql+psycopg2://).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --- PostgreSQL native enums -------------------------------------------------

subscription_tier_enum = ENUM(
    "basic",
    "pro",
    "premium",
    name="subscription_tier",
    create_type=True,
)

subscription_status_enum = ENUM(
    "active",
    "cancelled",
    "past_due",
    name="subscription_status",
    create_type=True,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    license_key_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    tier: Mapped[str] = mapped_column(subscription_tier_enum, nullable=False)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(subscription_status_enum, nullable=False)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class LicenseValidation(Base):
    __tablename__ = "license_validations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    license_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        "timestamp",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)


class Affiliate(Base):
    __tablename__ = "affiliates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    ref_code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    commission_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    stripe_connect_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payout_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    affiliate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("affiliates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    commission_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    monthly_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    commission_ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    self_referral_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        rest = url[len("postgres://") :]
        return f"postgresql+psycopg2://{rest}"
    if url.startswith("postgresql://"):
        rest = url[len("postgresql://") :]
        return f"postgresql+psycopg2://{rest}"
    return url


def create_engine_from_env(**kwargs: Any) -> Engine:
    """Build a SQLAlchemy engine using DATABASE_URL (psycopg2 driver)."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise ValueError("DATABASE_URL is not set")
    return create_engine(
        normalize_database_url(raw),
        connect_args={
            "connect_timeout": 10,
        },
        pool_pre_ping=True,
        **kwargs,
    )


def init_db(engine: Optional[Engine] = None, **engine_kwargs: Any) -> Engine:
    """
    Create all tables (and PostgreSQL enum types) if they do not exist.

    If ``engine`` is omitted, a new engine is created from DATABASE_URL.
    """
    eng = engine or create_engine_from_env(**engine_kwargs)
    Base.metadata.create_all(bind=eng)
    return eng

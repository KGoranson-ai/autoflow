"""initial license schema

Revision ID: 20260418_0001
Revises:
Create Date: 2026-04-18 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260418_0001"
down_revision = None
branch_labels = None
depends_on = None

subscription_tier = postgresql.ENUM(
    "basic",
    "pro",
    "premium",
    "solo",
    "team",
    name="subscription_tier",
)

subscription_status = postgresql.ENUM(
    "active",
    "cancelled",
    "past_due",
    name="subscription_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    subscription_tier.create(bind, checkfirst=True)
    subscription_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("license_key_hash", sa.String(length=64), nullable=False),
        sa.Column("tier", subscription_tier, nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("status", subscription_status, nullable=False),
        sa.Column("is_trial", sa.Boolean(), nullable=False),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("converted_from_trial", sa.Boolean(), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("license_key_hash"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    op.create_table(
        "license_validations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("license_key_hash", sa.String(length=64), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_license_validations_license_key_hash", "license_validations", ["license_key_hash"])

    op.create_table(
        "affiliates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("ref_code", sa.String(length=8), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("commission_percent", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("stripe_connect_id", sa.String(length=255), nullable=True),
        sa.Column("payout_email", sa.String(length=320), nullable=True),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("ref_code"),
    )
    op.create_index("ix_affiliates_email", "affiliates", ["email"])
    op.create_index("ix_affiliates_ref_code", "affiliates", ["ref_code"])

    op.create_table(
        "referrals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("affiliate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_email", sa.String(length=320), nullable=False),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("commission_percent", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("monthly_amount", sa.Integer(), nullable=False),
        sa.Column("commission_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("self_referral_attempt", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["affiliate_id"], ["affiliates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_referrals_affiliate_id", "referrals", ["affiliate_id"])

    op.create_table(
        "trial_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("tier", subscription_tier, nullable=False),
        sa.Column("license_key_hash", sa.String(length=64), nullable=False),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("converted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("license_key_hash"),
    )
    op.create_index("ix_trial_requests_email", "trial_requests", ["email"])


def downgrade() -> None:
    op.drop_index("ix_trial_requests_email", table_name="trial_requests")
    op.drop_table("trial_requests")
    op.drop_index("ix_referrals_affiliate_id", table_name="referrals")
    op.drop_table("referrals")
    op.drop_index("ix_affiliates_ref_code", table_name="affiliates")
    op.drop_index("ix_affiliates_email", table_name="affiliates")
    op.drop_table("affiliates")
    op.drop_index("ix_license_validations_license_key_hash", table_name="license_validations")
    op.drop_table("license_validations")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    subscription_status.drop(bind, checkfirst=True)
    subscription_tier.drop(bind, checkfirst=True)

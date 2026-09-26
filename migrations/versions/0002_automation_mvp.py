"""Create the automation MVP schema.

Revision ID: 0002_automation_mvp
Revises: 0001_telegram_users
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_automation_mvp"
down_revision = "0001_telegram_users"
branch_labels = None
depends_on = None


money = sa.Numeric(20, 5)


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_enabled", sa.Boolean(), nullable=False),
        sa.Column("cpm_enabled", sa.Boolean(), nullable=False),
        sa.Column("budget_enabled", sa.Boolean(), nullable=False),
        sa.Column("fraud_enabled", sa.Boolean(), nullable=False),
        sa.Column("target_cpa", money, nullable=False),
        sa.Column("min_cpm", money, nullable=False),
        sa.Column("max_cpm", money, nullable=False),
        sa.Column("budget_step", money, nullable=False),
        sa.Column("ad_budget_cap", money, nullable=False),
        sa.Column("account_reserve", money, nullable=False),
        sa.Column("refill_threshold", money, nullable=False),
        sa.Column("fraud_window_intervals", sa.Integer(), nullable=False),
        sa.Column("fraud_min_views", sa.Integer(), nullable=False),
        sa.Column("fraud_min_spend", money, nullable=False),
        sa.Column("monitor_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("optimizer_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("optimizer_window_hours", sa.Integer(), nullable=False),
        sa.Column("min_actions_increase", sa.Integer(), nullable=False),
        sa.Column("min_sample_multiplier", money, nullable=False),
        sa.Column("stop_cpa_multiplier", money, nullable=False),
        sa.Column("stop_spend_multiplier", money, nullable=False),
        sa.Column("change_cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_settings_singleton"),
        sa.CheckConstraint("target_cpa > 0", name="ck_settings_target_cpa_positive"),
        sa.CheckConstraint("min_cpm > 0 AND max_cpm >= min_cpm", name="ck_settings_cpm_bounds"),
        sa.CheckConstraint(
            "budget_step > 0 AND ad_budget_cap >= budget_step", name="ck_settings_budget_bounds"
        ),
        sa.CheckConstraint("monitor_interval_seconds >= 300", name="ck_settings_monitor_interval"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(
        sa.table(
            "settings",
            sa.column("id", sa.Integer()),
            sa.column("master_enabled", sa.Boolean()),
            sa.column("cpm_enabled", sa.Boolean()),
            sa.column("budget_enabled", sa.Boolean()),
            sa.column("fraud_enabled", sa.Boolean()),
            sa.column("target_cpa", money),
            sa.column("min_cpm", money),
            sa.column("max_cpm", money),
            sa.column("budget_step", money),
            sa.column("ad_budget_cap", money),
            sa.column("account_reserve", money),
            sa.column("refill_threshold", money),
            sa.column("fraud_window_intervals", sa.Integer()),
            sa.column("fraud_min_views", sa.Integer()),
            sa.column("fraud_min_spend", money),
            sa.column("monitor_interval_seconds", sa.Integer()),
            sa.column("optimizer_interval_seconds", sa.Integer()),
            sa.column("optimizer_window_hours", sa.Integer()),
            sa.column("min_actions_increase", sa.Integer()),
            sa.column("min_sample_multiplier", money),
            sa.column("stop_cpa_multiplier", money),
            sa.column("stop_spend_multiplier", money),
            sa.column("change_cooldown_minutes", sa.Integer()),
            sa.column("timezone", sa.String()),
            sa.column("version", sa.Integer()),
        ),
        [
            {
                "id": 1,
                "master_enabled": False,
                "cpm_enabled": True,
                "budget_enabled": True,
                "fraud_enabled": True,
                "target_cpa": 0.25,
                "min_cpm": 0.10,
                "max_cpm": 1.00,
                "budget_step": 0.10,
                "ad_budget_cap": 1.00,
                "account_reserve": 1.00,
                "refill_threshold": 0.02,
                "fraud_window_intervals": 3,
                "fraud_min_views": 20000,
                "fraud_min_spend": 0.50,
                "monitor_interval_seconds": 300,
                "optimizer_interval_seconds": 21600,
                "optimizer_window_hours": 72,
                "min_actions_increase": 3,
                "min_sample_multiplier": 3,
                "stop_cpa_multiplier": 2,
                "stop_spend_multiplier": 4,
                "change_cooldown_minutes": 90,
                "timezone": "Europe/Moscow",
                "version": 1,
            }
        ],
    )

    op.create_table(
        "stats_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("cpm", money, nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=False),
        sa.Column("actions", sa.BigInteger(), nullable=False),
        sa.Column("spent_budget", money, nullable=False),
        sa.Column("remaining_budget", money, nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snapshots_ad_captured", "stats_snapshots", ["account_id", "ad_id", "captured_at"]
    )

    op.create_table(
        "automation_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cycle_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=True),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("old_value", money, nullable=True),
        sa.Column("new_value", money, nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pending_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_automation_actions_cycle_id", "automation_actions", ["cycle_id"])
    op.create_index(
        "ix_actions_ad_created", "automation_actions", ["account_id", "ad_id", "created_at"]
    )
    op.create_index("ix_actions_status_pending", "automation_actions", ["status", "pending_until"])

    op.create_table(
        "budget_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("ad_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("budget_before", money, nullable=False),
        sa.Column("budget_after", money, nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["action_id"], ["automation_actions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_budget_operations_idempotency_key"),
    )
    op.create_index(
        "ix_budget_operations_status_created", "budget_operations", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("budget_operations")
    op.drop_table("automation_actions")
    op.drop_table("stats_snapshots")
    op.drop_table("settings")

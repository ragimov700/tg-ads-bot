"""Database models for snapshots, decisions, money operations and settings."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(20, 5)


class Base(DeclarativeBase):
    pass


class StatsSnapshot(Base):
    __tablename__ = "stats_snapshots"
    __table_args__ = (Index("ix_snapshots_ad_captured", "account_id", "ad_id", "captured_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cpm: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    views: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spent_budget: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remaining_budget: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    action_type: Mapped[str | None] = mapped_column(String(40))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutomationAction(Base):
    __tablename__ = "automation_actions"
    __table_args__ = (
        Index("ix_actions_ad_created", "account_id", "ad_id", "created_at"),
        Index("ix_actions_status_pending", "status", "pending_until"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ad_id: Mapped[int | None] = mapped_column(BigInteger)
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Decimal | None] = mapped_column(MONEY)
    new_value: Mapped[Decimal | None] = mapped_column(MONEY)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    pending_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BudgetOperation(Base):
    __tablename__ = "budget_operations"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_budget_operations_idempotency_key"),
        Index("ix_budget_operations_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("automation_actions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ad_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    budget_before: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    budget_after: Mapped[Decimal | None] = mapped_column(MONEY)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Settings(Base):
    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_settings_singleton"),
        CheckConstraint("target_cpa > 0", name="ck_settings_target_cpa_positive"),
        CheckConstraint("min_cpm > 0 AND max_cpm >= min_cpm", name="ck_settings_cpm_bounds"),
        CheckConstraint(
            "budget_step > 0 AND ad_budget_cap >= budget_step",
            name="ck_settings_budget_bounds",
        ),
        CheckConstraint("monitor_interval_seconds >= 300", name="ck_settings_monitor_interval"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    master_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cpm_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    budget_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fraud_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    target_cpa: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.25"), nullable=False)
    min_cpm: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.10"), nullable=False)
    max_cpm: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1.00"), nullable=False)
    budget_step: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.10"), nullable=False)
    ad_budget_cap: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1.00"), nullable=False)
    account_reserve: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("1.00"), nullable=False)
    refill_threshold: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal("0.02"), nullable=False
    )
    fraud_window_intervals: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    fraud_min_views: Mapped[int] = mapped_column(Integer, default=20_000, nullable=False)
    fraud_min_spend: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.50"), nullable=False)
    monitor_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    optimizer_interval_seconds: Mapped[int] = mapped_column(Integer, default=21_600, nullable=False)
    optimizer_window_hours: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    min_actions_increase: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    min_sample_multiplier: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal(3), nullable=False
    )
    stop_cpa_multiplier: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(2), nullable=False)
    stop_spend_multiplier: Mapped[Decimal] = mapped_column(
        MONEY, default=Decimal(4), nullable=False
    )
    change_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

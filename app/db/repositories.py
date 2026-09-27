"""Small async repositories shared by the bot and worker."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.automation.rules import RuleSettings
from app.db.models import AutomationAction, BudgetOperation, Settings, StatsSnapshot
from app.telegram_ads.schemas import Ad


def utcnow() -> datetime:
    return datetime.now(UTC)


class SettingsRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def get(self) -> Settings:
        async with self.sessions() as session:
            settings = await session.get(Settings, 1)
            if settings is None:
                raise RuntimeError("Settings row is missing; run Alembic migrations")
            return settings

    async def update(self, field: str, value: object) -> tuple[object, Settings]:
        if field not in EDITABLE_SETTINGS:
            raise ValueError(f"Setting {field!r} is not editable")
        async with self.sessions.begin() as session:
            settings = await session.get(Settings, 1, with_for_update=True)
            if settings is None:
                raise RuntimeError("Settings row is missing")
            old_value = getattr(settings, field)
            setattr(settings, field, value)
            settings.version += 1
            await session.flush()
            return old_value, settings


EDITABLE_SETTINGS = {
    "master_enabled",
    "cpm_enabled",
    "budget_enabled",
    "fraud_enabled",
    "target_cpa",
    "min_cpm",
    "max_cpm",
    "budget_step",
    "ad_budget_cap",
    "account_reserve",
    "refill_threshold",
    "fraud_window_intervals",
    "fraud_min_views",
    "fraud_min_spend",
    "monitor_interval_seconds",
    "optimizer_interval_seconds",
    "optimizer_window_hours",
}


def rule_settings(settings: Settings) -> RuleSettings:
    return RuleSettings(
        target_cpa=settings.target_cpa,
        min_cpm=settings.min_cpm,
        max_cpm=settings.max_cpm,
        budget_step=settings.budget_step,
        daily_budget_cap=settings.ad_budget_cap,
        account_reserve=settings.account_reserve,
        refill_threshold=settings.refill_threshold,
        fraud_min_views=settings.fraud_min_views,
        fraud_min_spend=settings.fraud_min_spend,
        min_actions_increase=settings.min_actions_increase,
        min_sample_multiplier=settings.min_sample_multiplier,
        stop_cpa_multiplier=settings.stop_cpa_multiplier,
        stop_spend_multiplier=settings.stop_spend_multiplier,
    )


class AutomationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def save_snapshots(self, account_id: str, ads: list[Ad], captured_at: datetime) -> None:
        async with self.sessions.begin() as session:
            session.add_all(
                StatsSnapshot(
                    account_id=account_id,
                    ad_id=ad.ad_id,
                    status=ad.status,
                    currency=ad.currency,
                    cpm=ad.cpm,
                    views=ad.views,
                    actions=ad.actions,
                    spent_budget=ad.spent_budget,
                    remaining_budget=ad.remaining_budget,
                    action_type=ad.action_type,
                    captured_at=captured_at,
                )
                for ad in ads
            )

    async def snapshot_at_or_before(
        self, account_id: str, ad_id: int, boundary: datetime
    ) -> StatsSnapshot | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(StatsSnapshot)
                .where(
                    StatsSnapshot.account_id == account_id,
                    StatsSnapshot.ad_id == ad_id,
                    StatsSnapshot.captured_at <= boundary,
                )
                .order_by(desc(StatsSnapshot.captured_at))
                .limit(1)
            )

    async def create_action(
        self,
        *,
        cycle_id: str,
        account_id: str,
        action_type: str,
        status: str,
        reason: str,
        ad_id: int | None = None,
        old_value: Decimal | None = None,
        new_value: Decimal | None = None,
        metrics: dict[str, object] | None = None,
        pending_until: datetime | None = None,
    ) -> AutomationAction:
        action = AutomationAction(
            id=str(uuid4()),
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad_id,
            action_type=action_type,
            status=status,
            reason=reason,
            old_value=old_value,
            new_value=new_value,
            metrics=metrics or {},
            pending_until=pending_until,
        )
        async with self.sessions.begin() as session:
            session.add(action)
        return action

    async def update_action(
        self,
        action_id: str,
        *,
        status: str,
        error: str | None = None,
        pending_until: datetime | None | object = ...,
    ) -> None:
        async with self.sessions.begin() as session:
            action = await session.get(AutomationAction, action_id, with_for_update=True)
            if action is None:
                return
            action.status = status
            action.error = error
            if pending_until is not ...:
                action.pending_until = pending_until  # type: ignore[assignment]

    async def action_exists(
        self,
        account_id: str,
        ad_id: int,
        action_type: str,
        *,
        reason: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
    ) -> bool:
        async with self.sessions() as session:
            statement = select(AutomationAction.id).where(
                AutomationAction.account_id == account_id,
                AutomationAction.ad_id == ad_id,
                AutomationAction.action_type == action_type,
            )
            if reason is not None:
                statement = statement.where(AutomationAction.reason == reason)
            if created_from is not None:
                statement = statement.where(AutomationAction.created_at >= created_from)
            if created_to is not None:
                statement = statement.where(AutomationAction.created_at < created_to)
            return (await session.scalar(statement.limit(1))) is not None

    async def successful_budget_total(
        self,
        account_id: str,
        ad_id: int,
        *,
        updated_from: datetime,
        updated_to: datetime,
    ) -> Decimal:
        async with self.sessions() as session:
            value = await session.scalar(
                select(func.coalesce(func.sum(BudgetOperation.amount), Decimal(0))).where(
                    BudgetOperation.account_id == account_id,
                    BudgetOperation.ad_id == ad_id,
                    BudgetOperation.status == "succeeded",
                    BudgetOperation.updated_at >= updated_from,
                    BudgetOperation.updated_at < updated_to,
                )
            )
            return Decimal(value or 0)

    async def failed_budget_operation_exists(
        self,
        account_id: str,
        ad_id: int,
        amount: Decimal,
        error: str,
        *,
        updated_from: datetime,
        updated_to: datetime,
    ) -> bool:
        async with self.sessions() as session:
            operation_id = await session.scalar(
                select(BudgetOperation.id).where(
                    BudgetOperation.account_id == account_id,
                    BudgetOperation.ad_id == ad_id,
                    BudgetOperation.amount == amount,
                    BudgetOperation.status == "failed",
                    BudgetOperation.error == error,
                    BudgetOperation.updated_at >= updated_from,
                    BudgetOperation.updated_at < updated_to,
                )
            )
            return operation_id is not None

    async def active_block(self, account_id: str, ad_id: int) -> AutomationAction | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(AutomationAction)
                .where(
                    AutomationAction.account_id == account_id,
                    AutomationAction.ad_id == ad_id,
                    AutomationAction.action_type == "fraud_pause",
                    AutomationAction.resolved_at.is_(None),
                )
                .order_by(desc(AutomationAction.created_at))
                .limit(1)
            )

    async def resolve_block(self, action_id: str) -> AutomationAction | None:
        async with self.sessions.begin() as session:
            action = await session.get(AutomationAction, action_id, with_for_update=True)
            if action is None or action.action_type != "fraud_pause":
                return None
            if action.resolved_at is None:
                action.resolved_at = utcnow()
            return action

    async def last_optimizer_action(self, account_id: str, ad_id: int) -> AutomationAction | None:
        optimizer_types = {
            "performance_warning",
            "performance_pause",
            "cpm_increase",
            "cpm_decrease",
            "optimizer_no_change",
            "insufficient_sample",
        }
        async with self.sessions() as session:
            return await session.scalar(
                select(AutomationAction)
                .where(
                    AutomationAction.account_id == account_id,
                    AutomationAction.ad_id == ad_id,
                    AutomationAction.action_type.in_(optimizer_types),
                )
                .order_by(desc(AutomationAction.created_at))
                .limit(1)
            )

    async def has_pending_mutation(self, account_id: str, ad_id: int) -> bool:
        now = utcnow()
        async with self.sessions() as session:
            action = await session.scalar(
                select(AutomationAction.id)
                .where(
                    AutomationAction.account_id == account_id,
                    AutomationAction.ad_id == ad_id,
                    AutomationAction.action_type.in_(
                        {"fraud_pause", "performance_pause", "cpm_increase", "cpm_decrease"}
                    ),
                    or_(
                        AutomationAction.status == "running",
                        AutomationAction.pending_until > now,
                    ),
                )
                .limit(1)
            )
            return action is not None

    async def has_pending_pause(self, account_id: str, ad_id: int) -> bool:
        now = utcnow()
        async with self.sessions() as session:
            action = await session.scalar(
                select(AutomationAction.id)
                .where(
                    AutomationAction.account_id == account_id,
                    AutomationAction.ad_id == ad_id,
                    AutomationAction.action_type.in_({"fraud_pause", "performance_pause"}),
                    or_(
                        AutomationAction.status == "running",
                        AutomationAction.pending_until > now,
                    ),
                )
                .limit(1)
            )
            return action is not None

    async def pending_edit_actions(self) -> list[AutomationAction]:
        now = utcnow()
        async with self.sessions() as session:
            rows = await session.scalars(
                select(AutomationAction).where(
                    AutomationAction.status.in_({"running", "succeeded"}),
                    AutomationAction.pending_until <= now,
                    AutomationAction.action_type.in_(
                        {"fraud_pause", "performance_pause", "cpm_increase", "cpm_decrease"}
                    ),
                )
            )
            return list(rows)

    async def create_budget_operation(
        self,
        *,
        cycle_id: str,
        account_id: str,
        ad_id: int,
        currency: str,
        amount: Decimal,
        budget_before: Decimal,
        reason: str,
        metrics: dict[str, object],
    ) -> tuple[AutomationAction, BudgetOperation]:
        action_id = str(uuid4())
        operation_id = str(uuid4())
        action = AutomationAction(
            id=action_id,
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad_id,
            action_type="budget_refill",
            status="planned",
            reason=reason,
            old_value=budget_before,
            new_value=budget_before + amount,
            metrics=metrics,
        )
        operation = BudgetOperation(
            id=operation_id,
            action_id=action_id,
            account_id=account_id,
            ad_id=ad_id,
            currency=currency,
            amount=amount,
            budget_before=budget_before,
            idempotency_key=str(uuid4()),
            status="planned",
        )
        async with self.sessions.begin() as session:
            session.add_all([action, operation])
        return action, operation

    async def update_budget_operation(
        self,
        operation_id: str,
        *,
        status: str,
        budget_after: Decimal | None = None,
        error: str | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            operation = await session.get(BudgetOperation, operation_id, with_for_update=True)
            if operation is None:
                return
            operation.status = status
            operation.error = error
            if budget_after is not None:
                operation.budget_after = budget_after
            action = await session.get(AutomationAction, operation.action_id, with_for_update=True)
            if action is not None:
                action.status = status
                action.error = error

    async def pending_budget_operations(self) -> list[BudgetOperation]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(BudgetOperation)
                .where(BudgetOperation.status.in_({"planned", "running"}))
                .order_by(BudgetOperation.created_at)
            )
            return list(rows)

    async def recent_actions(self, limit: int = 20) -> list[AutomationAction]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(AutomationAction).order_by(desc(AutomationAction.created_at)).limit(limit)
            )
            return list(rows)

    async def unresolved_blocks(self) -> list[AutomationAction]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(AutomationAction)
                .where(
                    AutomationAction.action_type == "fraud_pause",
                    AutomationAction.resolved_at.is_(None),
                )
                .order_by(desc(AutomationAction.created_at))
            )
            return list(rows)

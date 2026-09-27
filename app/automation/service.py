"""Worker orchestration for monitoring, budget management and CPA optimization."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncEngine

from app.automation.rules import (
    BudgetManager,
    CpaOptimizer,
    DecisionKind,
    FraudGuard,
    PerformanceSummary,
    SnapshotMetrics,
)
from app.db.models import AutomationAction, Settings
from app.db.repositories import (
    AutomationRepository,
    SettingsRepository,
    rule_settings,
    utcnow,
)
from app.db.session import advisory_lock
from app.telegram_ads.client import TelegramAdsClient
from app.telegram_ads.exceptions import TelegramAdsError
from app.telegram_ads.schemas import Account, Ad, AdStatItem

LOGGER = logging.getLogger(__name__)


def _budget_day_bounds(moment: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    timezone = ZoneInfo(timezone_name)
    local_start = moment.astimezone(timezone).replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


class AutomationNotifier(Protocol):
    async def fraud_paused(self, ad: Ad, action: AutomationAction) -> None: ...
    async def performance_paused(self, ad: Ad, action: AutomationAction) -> None: ...
    async def tracking_missing(self, ad: Ad) -> None: ...
    async def cycle_error(self, message: str) -> None: ...


class AutomationService:
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        settings: SettingsRepository,
        repository: AutomationRepository,
        ads: TelegramAdsClient,
        notifier: AutomationNotifier,
    ) -> None:
        self.engine = engine
        self.settings_repository = settings
        self.repository = repository
        self.ads = ads
        self.notifier = notifier
        self.fraud_guard = FraudGuard()
        self.budget_manager = BudgetManager()
        self.optimizer = CpaOptimizer()
        self._cycle_lock = asyncio.Lock()

    async def run_monitor_cycle(self) -> None:
        async with self._cycle_lock:
            await self._run_monitor_cycle_locked()

    async def _run_monitor_cycle_locked(self) -> None:
        async with advisory_lock(self.engine) as acquired:
            if not acquired:
                LOGGER.info("Skipping monitor cycle: another worker holds the lock")
                return
            cycle_id = str(uuid4())
            try:
                await self._reconcile_edits()
                await self._reconcile_budget_operations()
                settings = await self.settings_repository.get()
                account = await self.ads.get_account()
                ads = await self.ads.get_ads()
                captured_at = utcnow()
                await self.repository.save_snapshots(account.account_id, ads, captured_at)
                if settings.master_enabled and account.currency != "TON":
                    await self._record_cycle_error(
                        cycle_id,
                        account.account_id,
                        "Live automation requires TON settings",
                    )
                    return
                active_ads = [item for item in ads if item.status == "active"]
                budget_candidates = (
                    [
                        ad
                        for ad in active_ads
                        if ad.action_type and ad.remaining_budget <= settings.refill_threshold
                    ]
                    if settings.master_enabled and settings.budget_enabled
                    else []
                )
                budget_performance = await self._load_performance(
                    budget_candidates, settings.optimizer_window_hours
                )
                balance = account.remaining_budget
                for ad in active_ads:
                    try:
                        spent = await self._process_monitored_ad(
                            cycle_id,
                            captured_at,
                            account,
                            ad,
                            settings,
                            balance,
                            budget_performance.get(ad.ad_id),
                        )
                        balance -= spent
                    except Exception as exc:  # noqa: BLE001 - one broken ad must not stop the portfolio
                        await self._record_ad_error(cycle_id, account.account_id, ad.ad_id, exc)
            except Exception as exc:
                LOGGER.exception("Monitor cycle failed")
                await self._record_cycle_error(cycle_id, self.ads.account_id, type(exc).__name__)

    async def _process_monitored_ad(
        self,
        cycle_id: str,
        captured_at: datetime,
        account: Account,
        ad: Ad,
        settings: Settings,
        available_balance: Decimal,
        performance: PerformanceSummary | None,
    ) -> Decimal:
        if not ad.action_type:
            await self._ensure_tracking_notice(cycle_id, account.account_id, ad)
            return Decimal(0)
        if not settings.master_enabled:
            return Decimal(0)

        block = await self.repository.active_block(account.account_id, ad.ad_id)
        pending_pause = await self.repository.has_pending_pause(account.account_id, ad.ad_id)
        if settings.fraud_enabled and block is None and not pending_pause:
            boundary = captured_at - timedelta(
                seconds=settings.monitor_interval_seconds * settings.fraud_window_intervals
            )
            previous = await self.repository.snapshot_at_or_before(
                account.account_id, ad.ad_id, boundary
            )
            decision = self.fraud_guard.evaluate(
                SnapshotMetrics(previous.views, previous.actions, previous.spent_budget)
                if previous
                else None,
                SnapshotMetrics(ad.views, ad.actions, ad.spent_budget),
                rule_settings(settings),
            )
            if decision.kind == DecisionKind.FRAUD_PAUSE:
                action = await self._execute_edit(
                    cycle_id=cycle_id,
                    account_id=account.account_id,
                    ad=ad,
                    action_type="fraud_pause",
                    reason=decision.reason,
                    metrics=decision.metrics or {},
                    pause=True,
                    cooldown_minutes=settings.change_cooldown_minutes,
                )
                await self.notifier.fraud_paused(ad, action)
                return Decimal(0)

        if not settings.budget_enabled:
            return Decimal(0)
        if block is not None or await self.repository.has_pending_mutation(
            account.account_id, ad.ad_id
        ):
            return Decimal(0)
        automated_budget_today = Decimal(0)
        day_start, day_end = _budget_day_bounds(captured_at, settings.timezone)
        if ad.remaining_budget <= settings.refill_threshold:
            automated_budget_today = await self.repository.successful_budget_total(
                account.account_id,
                ad.ad_id,
                updated_from=day_start,
                updated_to=day_end,
            )
        decision = self.budget_manager.evaluate(
            spent_budget=ad.spent_budget,
            remaining_budget=ad.remaining_budget,
            automated_budget_today=automated_budget_today,
            account_balance=available_balance,
            currency=ad.currency,
            performance=performance,
            settings=rule_settings(settings),
        )
        if decision.kind == DecisionKind.BUDGET_CAP_REACHED:
            if not await self.repository.action_exists(
                account.account_id,
                ad.ad_id,
                "daily_budget_cap_reached",
                created_from=day_start,
                created_to=day_end,
            ):
                await self.repository.create_action(
                    cycle_id=cycle_id,
                    account_id=account.account_id,
                    ad_id=ad.ad_id,
                    action_type="daily_budget_cap_reached",
                    status="no_change",
                    reason=decision.reason,
                    metrics=decision.metrics,
                )
            return Decimal(0)
        if decision.kind == DecisionKind.BUDGET_BLOCKED:
            if not await self.repository.action_exists(
                account.account_id,
                ad.ad_id,
                "budget_refill_skipped",
                reason=decision.reason,
                created_from=day_start,
                created_to=day_end,
            ):
                await self.repository.create_action(
                    cycle_id=cycle_id,
                    account_id=account.account_id,
                    ad_id=ad.ad_id,
                    action_type="budget_refill_skipped",
                    status="no_change",
                    reason=decision.reason,
                    metrics=decision.metrics,
                )
            return Decimal(0)
        if decision.kind != DecisionKind.BUDGET_REFILL or decision.new_value is None:
            return Decimal(0)
        succeeded = await self._execute_budget_refill(
            cycle_id,
            account.account_id,
            ad,
            decision.new_value,
            decision.reason,
            decision.metrics or {},
        )
        return decision.new_value if succeeded else Decimal(0)

    async def run_optimizer_cycle(self) -> None:
        async with self._cycle_lock:
            await self._run_optimizer_cycle_locked()

    async def _run_optimizer_cycle_locked(self) -> None:
        async with advisory_lock(self.engine) as acquired:
            if not acquired:
                LOGGER.info("Skipping optimizer cycle: another worker holds the lock")
                return
            cycle_id = str(uuid4())
            try:
                await self._reconcile_edits()
                settings = await self.settings_repository.get()
                account = await self.ads.get_account()
                ads = [ad for ad in await self.ads.get_ads() if ad.status == "active"]
                if settings.master_enabled and account.currency != "TON":
                    await self._record_cycle_error(
                        cycle_id, account.account_id, "Live automation requires TON settings"
                    )
                    return
                performance = await self._load_performance(ads, settings.optimizer_window_hours)
                for ad in ads:
                    try:
                        if not ad.action_type:
                            await self._ensure_tracking_notice(cycle_id, account.account_id, ad)
                            continue
                        summary = performance.get(ad.ad_id)
                        if (
                            summary is None
                            or not settings.master_enabled
                            or not settings.cpm_enabled
                        ):
                            continue
                        if await self.repository.active_block(account.account_id, ad.ad_id):
                            continue
                        if await self.repository.has_pending_mutation(account.account_id, ad.ad_id):
                            continue
                        previous = await self.repository.last_optimizer_action(
                            account.account_id, ad.ad_id
                        )
                        decision = self.optimizer.evaluate(
                            current_cpm=ad.cpm,
                            currency=ad.currency,
                            performance=summary,
                            settings=rule_settings(settings),
                            previous_was_warning=(
                                previous is not None
                                and previous.action_type == "performance_warning"
                            ),
                        )
                        await self._apply_optimizer_decision(
                            cycle_id, account.account_id, ad, settings, decision
                        )
                    except Exception as exc:  # noqa: BLE001 - one broken ad must not stop the portfolio
                        await self._record_ad_error(cycle_id, account.account_id, ad.ad_id, exc)
            except Exception as exc:
                LOGGER.exception("Optimizer cycle failed")
                await self._record_cycle_error(cycle_id, self.ads.account_id, type(exc).__name__)

    async def _load_performance(
        self, ads: list[Ad], window_hours: int
    ) -> dict[int, PerformanceSummary]:
        now = int(datetime.now(UTC).timestamp())
        to_time = now - (now % 300)
        from_time = to_time - window_hours * 3600
        semaphore = asyncio.Semaphore(5)

        async def load(ad: Ad) -> tuple[int, PerformanceSummary | Exception]:
            try:
                async with semaphore:
                    rows = await self.ads.get_ad_stats(ad.ad_id, from_time, to_time, 300)
                return ad.ad_id, self._summarize(rows, ad.currency)
            except Exception as exc:  # noqa: BLE001 - preserve partial performance results
                return ad.ad_id, exc

        result: dict[int, PerformanceSummary] = {}
        for ad_id, value in await asyncio.gather(*(load(ad) for ad in ads)):
            if isinstance(value, Exception):
                LOGGER.warning(
                    "Could not load performance for ad %s: %s", ad_id, type(value).__name__
                )
            else:
                result[ad_id] = value
        return result

    @staticmethod
    def _summarize(rows: list[AdStatItem], currency: str) -> PerformanceSummary:
        if any(row.currency != currency for row in rows):
            raise TelegramAdsError("Statistics currency does not match the advertisement")
        return PerformanceSummary(
            spend=sum((row.spent_budget for row in rows), Decimal(0)),
            actions=sum(row.actions for row in rows),
            views=sum(row.views for row in rows),
        )

    async def _apply_optimizer_decision(
        self, cycle_id: str, account_id: str, ad: Ad, settings: Settings, decision
    ) -> None:
        metrics = decision.metrics or {}
        if decision.kind in {DecisionKind.CPM_INCREASE, DecisionKind.CPM_DECREASE}:
            await self._execute_edit(
                cycle_id=cycle_id,
                account_id=account_id,
                ad=ad,
                action_type=decision.kind.value,
                reason=decision.reason,
                metrics=metrics,
                cpm=decision.new_value,
                cooldown_minutes=settings.change_cooldown_minutes,
            )
            return
        if decision.kind == DecisionKind.PAUSE_PERFORMANCE:
            action = await self._execute_edit(
                cycle_id=cycle_id,
                account_id=account_id,
                ad=ad,
                action_type="performance_pause",
                reason=decision.reason,
                metrics=metrics,
                pause=True,
                cooldown_minutes=settings.change_cooldown_minutes,
            )
            await self.notifier.performance_paused(ad, action)
            return
        action_type = (
            "performance_warning"
            if decision.kind == DecisionKind.PERFORMANCE_WARNING
            else "insufficient_sample"
            if decision.kind == DecisionKind.INSUFFICIENT_SAMPLE
            else "optimizer_no_change"
        )
        action = await self.repository.create_action(
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad.ad_id,
            action_type=action_type,
            status="no_change",
            reason=decision.reason,
            old_value=ad.cpm,
            new_value=ad.cpm,
            metrics=metrics,
        )

    async def _execute_edit(
        self,
        *,
        cycle_id: str,
        account_id: str,
        ad: Ad,
        action_type: str,
        reason: str,
        metrics: dict[str, object],
        cooldown_minutes: int,
        cpm: Decimal | None = None,
        pause: bool = False,
    ) -> AutomationAction:
        pending_until = utcnow() + timedelta(minutes=cooldown_minutes)
        action = await self.repository.create_action(
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad.ad_id,
            action_type=action_type,
            status="planned",
            reason=reason,
            old_value=ad.cpm if cpm is not None else Decimal(0),
            new_value=cpm if cpm is not None else Decimal(1),
            metrics=metrics,
            pending_until=pending_until,
        )
        await self.repository.update_action(action.id, status="running")
        try:
            await self.ads.edit_ad(ad.ad_id, cpm=cpm, is_paused=True if pause else None)
        except TelegramAdsError as exc:
            if exc.ambiguous:
                await self.repository.update_action(
                    action.id, status="running", error="ambiguous_api_result"
                )
                action.status = "running"
                action.error = "ambiguous_api_result"
            else:
                await self.repository.update_action(
                    action.id, status="failed", error=exc.code or type(exc).__name__
                )
                action.status = "failed"
                action.error = exc.code or type(exc).__name__
        else:
            await self.repository.update_action(action.id, status="succeeded")
            action.status = "succeeded"
        return action

    async def _execute_budget_refill(
        self,
        cycle_id: str,
        account_id: str,
        ad: Ad,
        amount: Decimal,
        reason: str,
        metrics: dict[str, object],
    ) -> bool:
        _action, operation = await self.repository.create_budget_operation(
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad.ad_id,
            currency=ad.currency,
            amount=amount,
            budget_before=ad.remaining_budget,
            reason=reason,
            metrics=metrics,
        )
        await self.repository.update_budget_operation(operation.id, status="running")
        try:
            updated = await self.ads.increase_ad_budget(ad.ad_id, amount, operation.idempotency_key)
        except TelegramAdsError as exc:
            status = "running" if exc.ambiguous else "failed"
            await self.repository.update_budget_operation(
                operation.id, status=status, error=exc.code or type(exc).__name__
            )
            return False
        await self.repository.update_budget_operation(
            operation.id,
            status="succeeded",
            budget_after=updated.remaining_budget,
        )
        return True

    async def _reconcile_budget_operations(self) -> None:
        for operation in await self.repository.pending_budget_operations():
            if utcnow() - operation.created_at > timedelta(hours=24):
                await self.repository.update_budget_operation(
                    operation.id,
                    status="failed",
                    error="idempotency_window_expired",
                )
                continue
            try:
                updated = await self.ads.increase_ad_budget(
                    operation.ad_id, operation.amount, operation.idempotency_key
                )
            except TelegramAdsError as exc:
                if not exc.ambiguous:
                    await self.repository.update_budget_operation(
                        operation.id,
                        status="failed",
                        error=exc.code or type(exc).__name__,
                    )
            else:
                await self.repository.update_budget_operation(
                    operation.id,
                    status="succeeded",
                    budget_after=updated.remaining_budget,
                )

    async def _reconcile_edits(self) -> None:
        for action in await self.repository.pending_edit_actions():
            if action.ad_id is None:
                continue
            try:
                ads = await self.ads.get_ads_by_id([action.ad_id])
                ad = ads[0] if ads else None
                if ad is None:
                    raise TelegramAdsError("Advertisement disappeared")
                pause = action.action_type in {"fraud_pause", "performance_pause"}
                applied = (
                    ad.is_paused or ad.status in {"on_hold", "stopped"}
                    if pause
                    else ad.cpm == action.new_value
                )
                if applied:
                    await self.repository.update_action(
                        action.id, status="succeeded", error=None, pending_until=None
                    )
                    continue
                await self.ads.edit_ad(
                    ad.ad_id,
                    cpm=None if pause else action.new_value,
                    is_paused=True if pause else None,
                )
                await self.repository.update_action(
                    action.id,
                    status="running",
                    error=None,
                    pending_until=utcnow() + timedelta(minutes=30),
                )
            except TelegramAdsError as exc:
                if exc.ambiguous:
                    await self.repository.update_action(
                        action.id,
                        status="running",
                        error="ambiguous_api_result",
                        pending_until=utcnow() + timedelta(minutes=30),
                    )
                else:
                    await self.repository.update_action(
                        action.id,
                        status="failed",
                        error=exc.code or type(exc).__name__,
                        pending_until=None,
                    )

    async def _ensure_tracking_notice(self, cycle_id: str, account_id: str, ad: Ad) -> None:
        if await self.repository.action_exists(account_id, ad.ad_id, "tracking_missing"):
            return
        await self.repository.create_action(
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad.ad_id,
            action_type="tracking_missing",
            status="no_change",
            reason="action_type_is_missing",
            metrics={"title": ad.title},
        )
        await self.notifier.tracking_missing(ad)

    async def _record_ad_error(
        self, cycle_id: str, account_id: str, ad_id: int, exc: Exception
    ) -> None:
        LOGGER.exception("Failed to process ad %s", ad_id, exc_info=exc)
        action = await self.repository.create_action(
            cycle_id=cycle_id,
            account_id=account_id,
            ad_id=ad_id,
            action_type="worker_error",
            status="failed",
            reason="ad_processing_failed",
            metrics={},
        )
        await self.repository.update_action(
            action.id,
            status="failed",
            error=getattr(exc, "code", None) or type(exc).__name__,
        )

    async def _record_cycle_error(self, cycle_id: str, account_id: str, message: str) -> None:
        await self.repository.create_action(
            cycle_id=cycle_id,
            account_id=account_id,
            action_type="worker_error",
            status="failed",
            reason=message,
            metrics={},
        )
        await self.notifier.cycle_error(message)

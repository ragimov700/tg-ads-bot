"""Fresh aggregate statistics from Ads API plus automation counts from PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AutomationAction
from app.db.repositories import SettingsRepository
from app.statistics.metrics import aggregate, calculate_cpa
from app.telegram_ads.client import TelegramAdsClient


@dataclass(frozen=True, slots=True)
class PeriodStatistics:
    label: str
    currency: str
    spend: Decimal
    actions: int
    views: int
    cpa: Decimal | None
    active_ads: int
    target_cpa: Decimal
    automation_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class BalanceStatistics:
    currency: str
    available: Decimal
    on_ads: Decimal
    spent: Decimal
    reserve: Decimal


class StatisticsService:
    def __init__(
        self,
        ads: TelegramAdsClient,
        sessions: async_sessionmaker[AsyncSession],
        settings: SettingsRepository,
    ) -> None:
        self.ads = ads
        self.sessions = sessions
        self.settings_repository = settings

    async def period(self, days: int, *, previous: bool = False) -> PeriodStatistics:
        if days not in {1, 7, 30}:
            raise ValueError("days must be 1, 7 or 30")
        settings = await self.settings_repository.get()
        tz = ZoneInfo(settings.timezone)
        now_local = datetime.now(tz)
        today_start = datetime.combine(now_local.date(), time.min, tzinfo=tz)
        if previous:
            start_local = today_start - timedelta(days=1)
            end_local = today_start
            label = "Вчера"
        else:
            start_local = today_start - timedelta(days=days - 1)
            end_local = now_local
            label = "Сегодня" if days == 1 else f"Последние {days} дней"
        start_utc = start_local.astimezone(UTC)
        end_utc = end_local.astimezone(UTC)
        from_time = int(start_utc.timestamp())
        to_time = int(end_utc.timestamp())
        to_time -= to_time % 300

        account = await self.ads.get_account()
        rows = await self.ads.get_account_stats(from_time, to_time, 300)
        ads = await self.ads.get_ads()
        spend, actions, views = aggregate(rows, account.currency)
        counts = await self._automation_counts(start_utc, end_utc)
        return PeriodStatistics(
            label=label,
            currency=account.currency,
            spend=spend,
            actions=actions,
            views=views,
            cpa=calculate_cpa(spend, actions),
            active_ads=sum(ad.status == "active" for ad in ads),
            target_cpa=settings.target_cpa,
            automation_counts=counts,
        )

    async def balance(self) -> BalanceStatistics:
        settings = await self.settings_repository.get()
        account = await self.ads.get_account()
        return BalanceStatistics(
            currency=account.currency,
            available=account.remaining_budget,
            on_ads=account.ads_budget,
            spent=account.spent_budget,
            reserve=settings.account_reserve,
        )

    async def _automation_counts(self, start: datetime, end: datetime) -> dict[str, int]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(
                    AutomationAction.action_type,
                    AutomationAction.status,
                    func.count(AutomationAction.id),
                )
                .where(
                    AutomationAction.created_at >= start,
                    AutomationAction.created_at < end,
                )
                .group_by(AutomationAction.action_type, AutomationAction.status)
            )
        counts: dict[str, int] = {}
        for action_type, status, count in rows:
            if status == "succeeded" or (
                action_type == "optimizer_no_change" and status == "no_change"
            ):
                counts[action_type] = counts.get(action_type, 0) + count
        return counts

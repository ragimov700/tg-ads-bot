from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.automation.service import AutomationService
from app.db.models import Base, Settings
from app.db.repositories import AutomationRepository, SettingsRepository
from app.db.session import create_session_factory
from app.telegram_ads.exceptions import TelegramAdsError
from app.telegram_ads.schemas import Account, Ad, AdStatItem

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is not configured",
)


class FakeAds:
    account_id = "account"

    def __init__(self) -> None:
        self.ad = Ad(
            ad_id=1,
            title="Test",
            currency="TON",
            cpm=Decimal("0.20"),
            spent_budget=Decimal("0.50"),
            remaining_budget=Decimal("0.01"),
            views=1000,
            actions=3,
            action_type="join",
            status="active",
        )
        self.budget_calls: list[str] = []
        self.applied_keys: set[str] = set()
        self.edits: list[tuple[int, Decimal | None, bool | None]] = []

    async def get_account(self) -> Account:
        return Account(
            account_id="account",
            title="Test",
            currency="TON",
            spent_budget=Decimal(1),
            remaining_budget=Decimal(5),
            ads_budget=self.ad.remaining_budget,
        )

    async def get_ads(self) -> list[Ad]:
        return [self.ad]

    async def get_ads_by_id(self, _: list[int]) -> list[Ad]:
        return [self.ad]

    async def get_ad_stats(self, *args, **kwargs) -> list[AdStatItem]:
        return [
            AdStatItem(
                from_time=0,
                to_time=300,
                currency="TON",
                spent_budget=Decimal("0.50"),
                actions=3,
                views=1000,
            )
        ]

    async def edit_ad(
        self, ad_id: int, *, cpm: Decimal | None = None, is_paused: bool | None = None
    ) -> Ad:
        self.edits.append((ad_id, cpm, is_paused))
        return self.ad

    async def increase_ad_budget(self, ad_id: int, amount: Decimal, idempotency_key: str) -> Ad:
        self.budget_calls.append(idempotency_key)
        if idempotency_key not in self.applied_keys:
            self.applied_keys.add(idempotency_key)
            self.ad = self.ad.model_copy(
                update={"remaining_budget": self.ad.remaining_budget + amount}
            )
            raise TelegramAdsError("connection lost", ambiguous=True, retryable=True)
        return self.ad


class FakeNotifier:
    async def fraud_paused(self, *args) -> None:
        pass

    async def performance_paused(self, *args) -> None:
        pass

    async def tracking_missing(self, *args) -> None:
        pass

    async def cycle_error(self, *args) -> None:
        pass


@pytest.mark.asyncio
async def test_master_switch_and_budget_recovery_are_idempotent() -> None:
    database_url = make_url(os.environ["TEST_DATABASE_URL"])
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(database_url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = create_session_factory(engine)
        async with sessions.begin() as session:
            session.add(Settings(id=1))

        settings = SettingsRepository(sessions)
        repository = AutomationRepository(sessions)
        fake = FakeAds()
        service = AutomationService(
            engine=engine,
            settings=settings,
            repository=repository,
            ads=fake,  # type: ignore[arg-type]
            notifier=FakeNotifier(),
        )

        await service.run_optimizer_cycle()
        await service.run_monitor_cycle()
        assert fake.budget_calls == []
        assert fake.edits == []

        await settings.update("master_enabled", True)
        await settings.update("ad_budget_cap", Decimal("0.10"))
        await service.run_monitor_cycle()
        assert len(fake.budget_calls) == 1

        await service.run_monitor_cycle()
        assert len(fake.budget_calls) == 2
        assert fake.budget_calls[0] == fake.budget_calls[1]
        assert fake.ad.remaining_budget == Decimal("0.11000")

        fake.ad = fake.ad.model_copy(update={"remaining_budget": Decimal("0.01")})
        await service.run_monitor_cycle()
        assert len(fake.budget_calls) == 2
        assert any(
            action.action_type == "daily_budget_cap_reached"
            for action in await repository.recent_actions()
        )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()

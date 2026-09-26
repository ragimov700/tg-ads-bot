"""Run independent monitor and optimizer loops."""

from __future__ import annotations

import asyncio
import logging
import signal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.automation.service import AutomationService
from app.config import load_config
from app.db.repositories import AutomationRepository, SettingsRepository
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging
from app.telegram_ads.client import TelegramAdsClient
from app.worker.notifier import TelegramAutomationNotifier

LOGGER = logging.getLogger(__name__)


async def _loop(call, interval, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=await interval())
        except TimeoutError:
            await _safe_call(call)


async def _safe_call(call) -> None:
    try:
        await call()
    except Exception:
        LOGGER.exception("Unhandled worker cycle failure")


async def run_worker() -> None:
    config = load_config()
    configure_logging(config.log_level)
    engine = create_engine(config.database_url)
    sessions = create_session_factory(engine)
    settings_repository = SettingsRepository(sessions)
    repository = AutomationRepository(sessions)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        async with (
            Bot(
                config.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            ) as bot,
            TelegramAdsClient(
                config.ads_api_token,
                config.ads_account_id,
                base_url=config.ads_api_base_url,
            ) as ads,
        ):
            service = AutomationService(
                engine=engine,
                settings=settings_repository,
                repository=repository,
                ads=ads,
                notifier=TelegramAutomationNotifier(bot, config.admin_chat_id),
            )

            async def monitor_interval() -> int:
                return (await settings_repository.get()).monitor_interval_seconds

            async def optimizer_interval() -> int:
                return (await settings_repository.get()).optimizer_interval_seconds

            await _safe_call(service.run_optimizer_cycle)
            await _safe_call(service.run_monitor_cycle)
            await asyncio.gather(
                _loop(service.run_monitor_cycle, monitor_interval, stop),
                _loop(service.run_optimizer_cycle, optimizer_interval, stop),
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_worker())

"""Long-polling Telegram bot entrypoint."""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import build_router
from app.config import load_config
from app.db.repositories import AutomationRepository, SettingsRepository
from app.db.session import create_engine, create_session_factory
from app.logging import configure_logging
from app.statistics.service import StatisticsService
from app.telegram_ads.client import TelegramAdsClient


async def run_bot() -> None:
    config = load_config()
    configure_logging(config.log_level)
    engine = create_engine(config.database_url)
    sessions = create_session_factory(engine)
    settings = SettingsRepository(sessions)
    repository = AutomationRepository(sessions)
    dispatcher = Dispatcher()
    try:
        async with TelegramAdsClient(
            config.ads_api_token,
            config.ads_account_id,
            base_url=config.ads_api_base_url,
        ) as ads:
            statistics = StatisticsService(ads, sessions, settings)
            dispatcher.include_router(
                build_router(
                    admin_chat_id=config.admin_chat_id,
                    account_id=config.ads_account_id,
                    settings_repository=settings,
                    repository=repository,
                    statistics=statistics,
                    ads=ads,
                )
            )
            async with Bot(
                config.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            ) as bot:
                await dispatcher.start_polling(bot)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_bot())

"""Exceptional-event notifications sent by the worker."""

from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.db.models import AutomationAction
from app.telegram_ads.schemas import Ad


class TelegramAutomationNotifier:
    def __init__(self, bot: Bot, chat_id: int) -> None:
        self.bot = bot
        self.chat_id = chat_id

    async def fraud_paused(self, ad: Ad, action: AutomationAction) -> None:
        metrics = action.metrics
        failed = action.status == "failed"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Снять блокировку",
                        callback_data=f"unblock:{action.id}",
                    )
                ]
            ]
        )
        title = (
            "<b>🚨 Не удалось остановить подозрительное объявление</b>\n\n"
            if failed
            else "<b>⚠️ Объявление автоматически остановлено</b>\n\n"
        )
        guidance = (
            "Проверьте статус и остановите объявление вручную."
            if failed
            else "Объявление не будет запущено автоматически."
        )
        await self.bot.send_message(
            self.chat_id,
            title
            + f"{escape(ad.title or str(ad.ad_id))} (<code>{ad.ad_id}</code>)\n"
            + "Причина: подозрительный трафик\n"
            + f"Views за окно: {metrics.get('delta_views', 0)}\n"
            + f"Actions за окно: {metrics.get('delta_actions', 0)}\n"
            + f"Расход за окно: {escape(str(metrics.get('delta_spend', '0')))} {ad.currency}\n\n"
            + f"Пополнение бюджета заблокировано. {guidance}",
            reply_markup=keyboard,
        )

    async def performance_paused(self, ad: Ad, action: AutomationAction) -> None:
        metrics = action.metrics
        failed = action.status == "failed"
        title = (
            "<b>🚨 Не удалось остановить объявление с плохим CPA</b>\n\n"
            if failed
            else "<b>⛔ Объявление остановлено из-за CPA</b>\n\n"
        )
        guidance = " Остановите объявление вручную." if failed else ""
        await self.bot.send_message(
            self.chat_id,
            title
            + f"{escape(ad.title or str(ad.ad_id))} (<code>{ad.ad_id}</code>)\n"
            + f"CPA: {escape(str(metrics.get('cpa') or '—'))} {ad.currency}\n"
            + f"Target CPA: {escape(str(metrics.get('target_cpa')))} {ad.currency}\n"
            + f"Плохой результат подтверждён двумя проверками подряд.{guidance}",
        )

    async def tracking_missing(self, ad: Ad) -> None:
        await self.bot.send_message(
            self.chat_id,
            "<b>ℹ️ Автоматика пропустила объявление</b>\n\n"
            f"{escape(ad.title or str(ad.ad_id))} (<code>{ad.ad_id}</code>)\n"
            "В Telegram Ads не задан action_type. CPM, бюджет и статус объявления "
            "не будут изменяться автоматически.",
        )

    async def cycle_error(self, message: str) -> None:
        await self.bot.send_message(
            self.chat_id,
            f"<b>🚨 Ошибка автоматики</b>\n\nЦикл не завершён: <code>{escape(message)}</code>.",
        )

"""Thin aiogram handlers for the single-admin control panel."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.keyboards import confirmation, main_menu, statistics_periods
from app.bot.texts import SETTING_HELP, TOGGLE_HELP
from app.db.models import Settings
from app.db.repositories import AutomationRepository, SettingsRepository
from app.statistics.formatter import format_balance, format_period
from app.statistics.service import StatisticsService
from app.telegram_ads.client import TelegramAdsClient
from app.telegram_ads.exceptions import TelegramAdsError

TOGGLE_FIELDS = {
    "master": "master_enabled",
    "cpm": "cpm_enabled",
    "budget": "budget_enabled",
    "fraud": "fraud_enabled",
}


@dataclass(frozen=True, slots=True)
class EditableSetting:
    label: str
    unit: str
    integer: bool = False
    scale: int = 1

    def parse(self, raw: str) -> Decimal | int:
        try:
            number = Decimal(raw.strip().replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("Введите число") from exc
        if not number.is_finite():
            raise ValueError("Введите конечное число")
        value = number * self.scale
        if self.integer:
            if value != value.to_integral_value():
                raise ValueError("Введите целое значение")
            return int(value)
        return value

    def display(self, value: Decimal | int) -> str:
        shown = Decimal(value) / self.scale
        rendered = f"{shown:f}"
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered + (f" {self.unit}" if self.unit else "")


EDITABLE = {
    "target_cpa": EditableSetting("Target CPA", "TON"),
    "min_cpm": EditableSetting("Минимальный CPM", "TON"),
    "max_cpm": EditableSetting("Максимальный CPM", "TON"),
    "budget_step": EditableSetting("Шаг бюджета", "TON"),
    "ad_budget_cap": EditableSetting("Дневной лимит пополнений", "TON"),
    "account_reserve": EditableSetting("Резерв кабинета", "TON"),
    "refill_threshold": EditableSetting("Порог пополнения", "TON"),
    "fraud_window_intervals": EditableSetting("Окно FraudGuard", "интервалов", True),
    "fraud_min_views": EditableSetting("Минимум views", "", True),
    "fraud_min_spend": EditableSetting("Минимум расхода FraudGuard", "TON"),
    "monitor_interval_seconds": EditableSetting("Интервал мониторинга", "мин", True, 60),
    "optimizer_interval_seconds": EditableSetting("Интервал оптимизатора", "ч", True, 3600),
    "optimizer_window_hours": EditableSetting("Окно CPA", "ч", True),
}


class EditSetting(StatesGroup):
    value = State()
    confirm = State()


def _enabled(value: bool) -> str:
    return "✅" if value else "⛔"


def _automation_text(settings: Settings) -> str:
    return (
        "<b>⚙️ Автоматика</b>\n\n"
        f"Общий статус: {_enabled(settings.master_enabled)}\n"
        f"CPM: {_enabled(settings.cpm_enabled)}\n"
        f"Бюджет: {_enabled(settings.budget_enabled)}\n"
        f"FraudGuard: {_enabled(settings.fraud_enabled)}\n\n"
        f"Target CPA: <b>{settings.target_cpa} TON</b>\n"
        f"CPM: {settings.min_cpm}–{settings.max_cpm} TON\n"
        f"Шаг/дневной лимит: {settings.budget_step}/{settings.ad_budget_cap} TON\n"
        f"Резерв: {settings.account_reserve} TON\n"
        f"Мониторинг: {settings.monitor_interval_seconds // 60} мин\n"
        f"Оптимизатор: {settings.optimizer_interval_seconds // 3600} ч\n"
        f"Окно CPA: {settings.optimizer_window_hours} ч"
    )


def _automation_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_enabled(settings.master_enabled)} Общий статус",
                callback_data="toggle:master",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"{_enabled(settings.cpm_enabled)} CPM", callback_data="toggle:cpm"
            ),
            InlineKeyboardButton(
                text=f"{_enabled(settings.budget_enabled)} Бюджет", callback_data="toggle:budget"
            ),
            InlineKeyboardButton(
                text=f"{_enabled(settings.fraud_enabled)} Fraud", callback_data="toggle:fraud"
            ),
        ],
    ]
    fields = list(EDITABLE.items())
    for index in range(0, len(fields), 2):
        rows.append(
            [
                InlineKeyboardButton(text=item.label, callback_data=f"edit:{name}")
                for name, item in fields[index : index + 2]
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _validate_value(field: str, value: Decimal | int, settings: Settings) -> None:
    if Decimal(value) < 0:
        raise ValueError("Значение не может быть отрицательным")
    if (
        field
        in {"target_cpa", "min_cpm", "max_cpm", "budget_step", "ad_budget_cap", "fraud_min_spend"}
        and Decimal(value) <= 0
    ):
        raise ValueError("Значение должно быть больше нуля")
    if field == "min_cpm" and Decimal(value) > settings.max_cpm:
        raise ValueError("Минимальный CPM не может превышать максимальный")
    if field == "max_cpm" and Decimal(value) < settings.min_cpm:
        raise ValueError("Максимальный CPM не может быть меньше минимального")
    if field == "budget_step" and Decimal(value) > settings.ad_budget_cap:
        raise ValueError("Шаг бюджета не может превышать дневной лимит пополнений")
    if field == "ad_budget_cap" and Decimal(value) < settings.budget_step:
        raise ValueError("Дневной лимит не может быть меньше шага бюджета")
    if field == "refill_threshold" and Decimal(value) > settings.budget_step:
        raise ValueError("Порог пополнения не может превышать шаг бюджета")
    if field == "monitor_interval_seconds" and (int(value) < 300 or int(value) % 300):
        raise ValueError("Интервал мониторинга должен быть кратен 5 минутам")
    if field == "optimizer_interval_seconds" and int(value) < 3600:
        raise ValueError("Интервал оптимизатора должен быть не меньше 1 часа")
    if field == "optimizer_window_hours" and not 1 <= int(value) <= 720:
        raise ValueError("Окно CPA должно быть от 1 до 720 часов")
    if field == "fraud_window_intervals" and not 1 <= int(value) <= 24:
        raise ValueError("Окно FraudGuard должно быть от 1 до 24 интервалов")
    if field == "fraud_min_views" and int(value) < 1:
        raise ValueError("Минимум views должен быть больше нуля")


def _validate_configuration(settings: Settings) -> None:
    if settings.target_cpa <= 0:
        raise ValueError("Target CPA должен быть больше нуля")
    if settings.min_cpm <= 0 or settings.max_cpm < settings.min_cpm:
        raise ValueError("Некорректные границы CPM")
    if settings.budget_step <= 0 or settings.ad_budget_cap < settings.budget_step:
        raise ValueError("Некорректные параметры бюджета")
    if settings.refill_threshold > settings.budget_step:
        raise ValueError("Порог пополнения превышает шаг бюджета")
    if settings.monitor_interval_seconds < 300 or settings.monitor_interval_seconds % 300:
        raise ValueError("Мониторинг должен быть кратен пяти минутам")


async def _record_setting_change(
    repository: AutomationRepository,
    account_id: str,
    field: str,
    old: object,
    new: object,
) -> None:
    def decimal_or_none(value: object) -> Decimal | None:
        if isinstance(value, bool):
            return Decimal(int(value))
        if isinstance(value, (int, Decimal)):
            return Decimal(value)
        return None

    await repository.create_action(
        cycle_id=str(uuid4()),
        account_id=account_id,
        action_type="settings_change",
        status="succeeded",
        reason=field,
        old_value=decimal_or_none(old),
        new_value=decimal_or_none(new),
        metrics={"field": field, "old": str(old), "new": str(new)},
    )


def build_router(
    *,
    admin_chat_id: int,
    account_id: str,
    settings_repository: SettingsRepository,
    repository: AutomationRepository,
    statistics: StatisticsService,
    ads: TelegramAdsClient,
) -> Router:
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE, F.chat.id == admin_chat_id)
    router.callback_query.filter(F.message.chat.id == admin_chat_id)

    async def show_automation(message: Message) -> None:
        settings = await settings_repository.get()
        await message.answer(
            _automation_text(settings), reply_markup=_automation_keyboard(settings)
        )

    async def show_stats(message: Message, days: int = 1, previous: bool = False) -> None:
        try:
            result = await statistics.period(days, previous=previous)
        except (TelegramAdsError, ValueError):
            await message.answer("Не удалось получить статистику Telegram Ads.")
            return
        await message.answer(format_period(result), reply_markup=statistics_periods())

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Пульт автоматического управления Telegram Ads готов. "
            "При первом запуске общий переключатель выключен.",
            reply_markup=main_menu(),
        )

    @router.message(Command("cancel"))
    async def cancel_message(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Изменение отменено.", reply_markup=main_menu())

    @router.message(F.text == "📊 Статистика")
    async def stats_message(message: Message) -> None:
        await show_stats(message)

    @router.callback_query(F.data.startswith("stats:"))
    async def stats_callback(callback: CallbackQuery) -> None:
        key = callback.data.split(":", 1)[1]
        if callback.message is not None:
            if key == "yesterday":
                await show_stats(callback.message, 1, True)
            else:
                await show_stats(callback.message, 1 if key == "today" else int(key))
        await callback.answer()

    @router.message(F.text == "⚙️ Автоматика")
    async def automation_message(message: Message) -> None:
        await show_automation(message)

    @router.callback_query(F.data.startswith("toggle:"))
    async def toggle(callback: CallbackQuery) -> None:
        key = callback.data.split(":", 1)[1]
        field = TOGGLE_FIELDS.get(key)
        if field is None:
            await callback.answer("Неизвестная настройка", show_alert=True)
            return
        current = await settings_repository.get()
        new_value = not getattr(current, field)
        help_text = TOGGLE_HELP[key]
        if callback.message is not None:
            await callback.message.answer(
                f"<b>{'Включение' if new_value else 'Выключение'} настройки</b>\n\n"
                f"{help_text.render()}\n\n"
                f"Сейчас: <b>{'включено' if not new_value else 'выключено'}</b>\n"
                f"После изменения: <b>{'включено' if new_value else 'выключено'}</b>\n\n"
                "Применить изменение?",
                reply_markup=confirmation(
                    f"toggle_confirm:{key}:{int(new_value)}", "toggle_cancel"
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("toggle_confirm:"))
    async def toggle_confirm(callback: CallbackQuery) -> None:
        _, key, raw_value = callback.data.split(":", 2)
        field = TOGGLE_FIELDS.get(key)
        if field is None or raw_value not in {"0", "1"}:
            await callback.answer("Неизвестная настройка", show_alert=True)
            return
        new_value = raw_value == "1"
        current = await settings_repository.get()
        if getattr(current, field) == new_value:
            await callback.answer("Это значение уже установлено", show_alert=True)
            return
        if field == "master_enabled" and new_value:
            try:
                _validate_configuration(current)
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            try:
                account = await ads.get_account()
            except TelegramAdsError:
                await callback.answer("Кабинет Telegram Ads недоступен", show_alert=True)
                return
            if account.currency != "TON":
                await callback.answer(
                    "Стартовые денежные настройки рассчитаны на TON", show_alert=True
                )
                return
        old, updated = await settings_repository.update(field, new_value)
        await _record_setting_change(repository, account_id, field, old, new_value)
        if callback.message is not None:
            await callback.message.edit_text(
                _automation_text(updated), reply_markup=_automation_keyboard(updated)
            )
        await callback.answer("Настройка сохранена")

    @router.callback_query(F.data.startswith("edit:"))
    async def edit_setting(callback: CallbackQuery, state: FSMContext) -> None:
        field = callback.data.split(":", 1)[1]
        spec = EDITABLE.get(field)
        if spec is None:
            await callback.answer("Неизвестная настройка", show_alert=True)
            return
        settings = await settings_repository.get()
        await state.set_state(EditSetting.value)
        await state.update_data(field=field)
        if callback.message is not None:
            help_text = SETTING_HELP[field]
            await callback.message.answer(
                f"<b>{spec.label}</b>\n\n"
                f"{help_text.render()}\n\n"
                f"Сейчас: <b>{spec.display(getattr(settings, field))}</b>\n\n"
                "Введите новое значение.\n"
                "Для отмены: /cancel"
            )
        await callback.answer()

    @router.message(EditSetting.value)
    async def receive_setting(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        field = data.get("field")
        spec = EDITABLE.get(field)
        if spec is None or message.text is None:
            await state.clear()
            await message.answer("Изменение отменено.")
            return
        try:
            value = spec.parse(message.text)
            settings = await settings_repository.get()
            _validate_value(field, value, settings)
        except ValueError as exc:
            await message.answer(f"{escape(str(exc))}. Попробуйте ещё раз или используйте /cancel.")
            return
        await state.set_state(EditSetting.confirm)
        await state.update_data(value=str(value))
        await message.answer(
            f"Изменить «{spec.label}» с <b>{spec.display(getattr(settings, field))}</b> "
            f"на <b>{spec.display(value)}</b>?",
            reply_markup=confirmation("setting_confirm", "setting_cancel"),
        )

    @router.callback_query(F.data == "setting_confirm", EditSetting.confirm)
    async def confirm_setting(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        field = data.get("field")
        spec = EDITABLE.get(field)
        if spec is None:
            await state.clear()
            await callback.answer("Изменение устарело", show_alert=True)
            return
        value = int(data["value"]) if spec.integer else Decimal(data["value"])
        current = await settings_repository.get()
        try:
            _validate_value(field, value, current)
        except ValueError as exc:
            await state.clear()
            await callback.answer(str(exc), show_alert=True)
            return
        old, _ = await settings_repository.update(field, value)
        await _record_setting_change(repository, account_id, field, old, value)
        await state.clear()
        if callback.message is not None:
            await callback.message.answer("Настройка сохранена.", reply_markup=main_menu())
        await callback.answer()

    @router.callback_query(F.data.in_({"setting_cancel", "toggle_cancel", "cancel"}))
    async def cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.answer("Отменено")

    @router.message(F.text == "💰 Баланс")
    async def balance(message: Message) -> None:
        try:
            result = await statistics.balance()
        except TelegramAdsError:
            await message.answer("Не удалось получить баланс Telegram Ads.")
            return
        await message.answer(format_balance(result))

    @router.message(F.text == "🚨 События")
    async def events(message: Message) -> None:
        blocks = await repository.unresolved_blocks()
        actions = await repository.recent_actions(20)
        lines = ["<b>🚨 События</b>", ""]
        if blocks:
            lines.append(f"Активных блокировок: <b>{len(blocks)}</b>")
            lines.append("")
        for action in actions:
            ad = f"ad <code>{action.ad_id}</code> · " if action.ad_id is not None else ""
            lines.append(
                f"{action.created_at:%d.%m %H:%M} · {ad}"
                f"{escape(action.action_type)} · {escape(action.status)}"
            )
        keyboard = (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"Снять блок ad {item.ad_id}", callback_data=f"unblock:{item.id}"
                        )
                    ]
                    for item in blocks[:10]
                ]
            )
            if blocks
            else None
        )
        await message.answer("\n".join(lines), reply_markup=keyboard)

    @router.callback_query(F.data.startswith("unblock:"))
    async def unblock_request(callback: CallbackQuery) -> None:
        action_id = callback.data.split(":", 1)[1]
        if callback.message is not None:
            await callback.message.answer(
                "Снять внутреннюю блокировку? Объявление останется на паузе.",
                reply_markup=confirmation(f"unblock_confirm:{action_id}"),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("unblock_confirm:"))
    async def unblock_confirm(callback: CallbackQuery) -> None:
        action_id = callback.data.split(":", 1)[1]
        action = await repository.resolve_block(action_id)
        if action is None:
            await callback.answer("Блокировка не найдена", show_alert=True)
            return
        await _record_setting_change(repository, account_id, "fraud_unblock", 1, 0)
        if callback.message is not None:
            await callback.message.answer(
                f"Блокировка ad <code>{action.ad_id}</code> снята. Объявление осталось на паузе."
            )
        await callback.answer("Блокировка снята")

    @router.message()
    async def fallback(message: Message) -> None:
        await message.answer("Выберите раздел на клавиатуре.", reply_markup=main_menu())

    return router

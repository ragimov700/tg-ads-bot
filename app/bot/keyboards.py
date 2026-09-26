"""Reply and inline keyboards for the compact admin UI."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⚙️ Автоматика")],
            [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="🚨 События")],
        ],
        resize_keyboard=True,
    )


def statistics_periods() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="stats:today"),
                InlineKeyboardButton(text="Вчера", callback_data="stats:yesterday"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="stats:7"),
                InlineKeyboardButton(text="30 дней", callback_data="stats:30"),
            ],
        ]
    )


def confirmation(confirm_data: str, cancel_data: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data=confirm_data),
                InlineKeyboardButton(text="Отмена", callback_data=cancel_data),
            ]
        ]
    )

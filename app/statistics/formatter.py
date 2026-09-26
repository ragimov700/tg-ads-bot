"""Russian Telegram-safe formatting for aggregate reports."""

from decimal import ROUND_HALF_UP, Decimal
from html import escape

from app.statistics.service import BalanceStatistics, PeriodStatistics


def money(value: Decimal, currency: str) -> str:
    precision = Decimal(1) if currency == "XTR" else Decimal("0.00001")
    rendered = f"{value.quantize(precision, rounding=ROUND_HALF_UP):f}".rstrip("0").rstrip(".")
    return f"{rendered or '0'} {currency}"


def format_period(stats: PeriodStatistics) -> str:
    cpa = money(stats.cpa, stats.currency) if stats.cpa is not None else "—"
    counts = stats.automation_counts
    return (
        f"<b>📊 {escape(stats.label)}</b>\n\n"
        f"Расход: <b>{money(stats.spend, stats.currency)}</b>\n"
        f"Actions: <b>{stats.actions}</b>\n"
        f"Views: <b>{stats.views}</b>\n"
        f"CPA: <b>{cpa}</b>\n"
        f"Целевой CPA: <b>{money(stats.target_cpa, stats.currency)}</b>\n\n"
        f"Активных объявлений: <b>{stats.active_ads}</b>\n\n"
        "<b>Автоматика</b>\n"
        f"CPM повышен: {counts.get('cpm_increase', 0)}\n"
        f"CPM понижен: {counts.get('cpm_decrease', 0)}\n"
        f"Без изменений: {counts.get('optimizer_no_change', 0)}\n"
        f"Бюджет пополнен: {counts.get('budget_refill', 0)}\n"
        f"Остановлено как подозрительное: {counts.get('fraud_pause', 0)}\n"
        f"Достигли лимита бюджета: {counts.get('budget_cap_reached', 0)}"
    )


def format_balance(stats: BalanceStatistics) -> str:
    return (
        "<b>💰 Баланс кабинета</b>\n\n"
        f"Доступно: <b>{money(stats.available, stats.currency)}</b>\n"
        f"На бюджетах объявлений: <b>{money(stats.on_ads, stats.currency)}</b>\n"
        f"Потрачено всего: <b>{money(stats.spent, stats.currency)}</b>\n"
        f"Неприкосновенный резерв: <b>{money(stats.reserve, stats.currency)}</b>"
    )

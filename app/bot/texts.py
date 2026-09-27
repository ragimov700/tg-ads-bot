"""User-facing explanations for automation controls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ControlHelp:
    description: str
    example: str

    def render(self) -> str:
        return f"{self.description}\n\n<b>Пример:</b> {self.example}"


EVENT_TYPE_LABELS: dict[str, str] = {
    "fraud_pause": "Объявление остановлено FraudGuard",
    "performance_pause": "Объявление остановлено из-за плохого CPA",
    "performance_warning": "Предупреждение о плохом CPA",
    "cpm_increase": "CPM повышен",
    "cpm_decrease": "CPM понижен",
    "optimizer_no_change": "CPM оставлен без изменений",
    "insufficient_sample": "Недостаточно данных для изменения CPM",
    "budget_refill": "Бюджет пополнен",
    "budget_refill_skipped": "Пополнение пропущено",
    "daily_budget_cap_reached": "Дневной лимит пополнений достигнут",
    "budget_cap_reached": "Лимит бюджета достигнут",
    "tracking_missing": "Отслеживание actions недоступно",
    "worker_error": "Ошибка автоматики",
    "settings_change": "Настройка изменена",
}

BUDGET_SKIP_LABELS: dict[str, str] = {
    "performance_data_unavailable": "Пополнение пропущено: статистика CPA недоступна",
    "cpa_is_not_acceptable": "Пополнение пропущено: CPA выше допустимого",
    "account_reserve_would_be_violated": (
        "Пополнение пропущено: недостаточно средств сверх резерва"
    ),
}

EVENT_STATUS_LABELS: dict[str, str] = {
    "planned": "запланировано",
    "running": "выполняется",
    "succeeded": "успешно",
    "failed": "ошибка",
    "no_change": "без изменений",
}

EVENT_ERROR_LABELS: dict[str, str] = {
    "AD_RESULT_BUDGET_TOO_SMALL": ("сумма пополнения слишком мала — увеличьте «Шаг бюджета»"),
}

SETTING_EVENT_LABELS: dict[str, str] = {
    "master_enabled": "Общий статус",
    "cpm_enabled": "Автоматическое управление CPM",
    "budget_enabled": "Автоматическое пополнение бюджета",
    "fraud_enabled": "FraudGuard",
    "target_cpa": "Target CPA",
    "min_cpm": "Минимальный CPM",
    "max_cpm": "Максимальный CPM",
    "budget_step": "Шаг бюджета",
    "ad_budget_cap": "Дневной лимит пополнений",
    "account_reserve": "Резерв кабинета",
    "refill_threshold": "Порог пополнения",
    "fraud_window_intervals": "Окно FraudGuard",
    "fraud_min_views": "Минимум просмотров FraudGuard",
    "fraud_min_spend": "Минимальный расход FraudGuard",
    "monitor_interval_seconds": "Интервал мониторинга",
    "optimizer_interval_seconds": "Интервал оптимизатора",
    "optimizer_window_hours": "Окно CPA",
    "fraud_unblock": "Блокировка FraudGuard снята",
}


def event_type_label(action_type: str, reason: str, status: str | None = None) -> str:
    if action_type == "budget_refill" and status not in {None, "succeeded"}:
        return "Пополнение бюджета"
    if action_type == "budget_refill_skipped":
        return BUDGET_SKIP_LABELS.get(reason, EVENT_TYPE_LABELS[action_type])
    if action_type == "settings_change":
        setting = SETTING_EVENT_LABELS.get(reason)
        if reason == "fraud_unblock":
            return setting or EVENT_TYPE_LABELS[action_type]
        if setting is not None:
            return f"Изменена настройка «{setting}»"
    return EVENT_TYPE_LABELS.get(action_type, action_type)


def event_status_label(status: str) -> str:
    return EVENT_STATUS_LABELS.get(status, status)


def event_error_label(error: str | None) -> str | None:
    if error is None:
        return None
    return EVENT_ERROR_LABELS.get(error)


TOGGLE_HELP: dict[str, ControlHelp] = {
    "master": ControlHelp(
        "Главный выключатель автоматики. Когда он выключен, бот продолжает собирать "
        "статистику, но не меняет CPM, не пополняет бюджеты и не ставит объявления на "
        "паузу. Сами объявления в Telegram Ads при этом не останавливаются.",
        "общий статус выключен — статистика обновляется каждые 5 минут, но бот не "
        "выполняет ни одного изменяющего запроса.",
    ),
    "cpm": ControlHelp(
        "Разрешает оптимизатору менять CPM по фактическому CPA. Решение принимается по "
        "окну статистики и ограничивается минимальным и максимальным CPM. После изменения "
        "новая корректировка этого объявления блокируется на 90 минут.",
        "при Target CPA 0.25 TON и CPA 0.18 TON ставка может вырасти на 5%, но не выше "
        "максимального CPM.",
    ),
    "budget": ControlHelp(
        "Разрешает пополнять объявления с низким остатком. Перед пополнением бот проверяет "
        "CPA, дневной лимит автоматических пополнений, резерв кабинета, блокировки и "
        "незавершённые операции.",
        "если осталось 0.02 TON, шаг равен 0.10 TON и все проверки пройдены, бот добавит "
        "до 0.10 TON.",
    ),
    "fraud": ControlHelp(
        "Включает защиту от подозрительного трафика. Если за заданное окно нет новых "
        "actions, но достигнуты пороги просмотров и расхода, бот ставит объявление на паузу "
        "и запрещает его дальнейшее пополнение.",
        "за 3 интервала получено 25 000 просмотров, потрачено 0.60 TON и нет actions — "
        "объявление будет остановлено и появится событие.",
    ),
}


SETTING_HELP: dict[str, ControlHelp] = {
    "target_cpa": ControlHelp(
        "Целевая стоимость одного действия: расход делится на количество actions. По этому "
        "значению бот оценивает эффективность объявления, меняет CPM и разрешает или "
        "запрещает пополнение бюджета.",
        "при расходе 1 TON и 4 actions CPA равен 0.25 TON. Если Target CPA тоже 0.25 TON, "
        "результат считается нормальным.",
    ),
    "min_cpm": ControlHelp(
        "Нижняя граница CPM. Оптимизатор не сможет уменьшить ставку ниже этого значения, "
        "даже если CPA слишком высокий.",
        "при минимуме 0.10 TON попытка снизить CPM с 0.10 TON ещё на 10% оставит ставку "
        "на уровне 0.10 TON.",
    ),
    "max_cpm": ControlHelp(
        "Верхняя граница CPM. Оптимизатор не сможет повысить ставку выше этого значения, "
        "даже если объявление показывает хороший CPA.",
        "при максимуме 1.00 TON повышение ставки 0.98 TON на 5% будет ограничено "
        "значением 1.00 TON.",
    ),
    "budget_step": ControlHelp(
        "Максимальная сумма одного автоматического пополнения. Фактическая сумма может "
        "быть меньше, если до дневного лимита осталось меньше установленного шага. "
        "Telegram Ads отклоняет слишком маленькие пополнения; в текущем TON-кабинете "
        "используйте шаг не меньше 1.00 TON.",
        "при шаге 1.00 TON и дневном лимите 2.00 TON бот сможет добавить по 1.00 TON "
        "не более двух раз за день.",
    ),
    "ad_budget_cap": ControlHelp(
        "Максимальная сумма, которую бот может автоматически добавить одному объявлению "
        "за календарный день. Исторический расход и ручные пополнения в неё не входят. "
        "Счётчик сбрасывается в полночь по часовому поясу настроек.",
        "при дневном лимите 1.00 TON и шаге 0.50 TON бот сможет выполнить не больше двух "
        "полных автоматических пополнений одному объявлению за день.",
    ),
    "account_reserve": ControlHelp(
        "Минимальный баланс кабинета, который бот обязан оставить после пополнения "
        "объявлений. Ограничение действует только на автоматические операции бота.",
        "при балансе 1.07 TON и резерве 1.00 TON бот не выполнит пополнение на 0.10 TON.",
    ),
    "refill_threshold": ControlHelp(
        "Остаток бюджета, при котором бот начинает рассматривать пополнение объявления. "
        "Достижение порога не гарантирует операцию: остальные проверки всё равно должны "
        "пройти.",
        "при пороге 0.02 TON объявление с остатком 0.03 TON не пополняется, а с остатком "
        "0.02 TON уже проверяется для пополнения.",
    ),
    "fraud_window_intervals": ControlHelp(
        "Количество завершённых интервалов мониторинга, за которые FraudGuard сравнивает "
        "прирост actions, просмотров и расхода.",
        "при мониторинге раз в 5 минут окно из 3 интервалов охватывает последние 15 минут.",
    ),
    "fraud_min_views": ControlHelp(
        "Минимальный прирост просмотров за окно FraudGuard. Пауза возможна только при "
        "одновременном отсутствии actions и достижении порога расхода.",
        "при пороге 20 000 прирост 19 999 просмотров сам по себе не запустит блокировку.",
    ),
    "fraud_min_spend": ControlHelp(
        "Минимальный расход за окно FraudGuard. Используется вместе с порогом просмотров "
        "и условием отсутствия новых actions.",
        "при пороге 0.50 TON расход 0.60 TON без actions может привести к паузе, если также "
        "достигнут минимум просмотров.",
    ),
    "monitor_interval_seconds": ControlHelp(
        "Как часто бот получает объявления, сохраняет снимки и проверяет бюджеты и "
        "FraudGuard. Значение задаётся в минутах и должно быть кратно 5.",
        "интервал 5 минут означает 12 проверок в час.",
    ),
    "optimizer_interval_seconds": ControlHelp(
        "Как часто выполняется анализ CPA для изменения CPM и возможной остановки "
        "неэффективных объявлений. Значение задаётся в часах; минимум — 1 час.",
        "интервал 6 часов запускает оптимизатор примерно четыре раза в сутки.",
    ),
    "optimizer_window_hours": ControlHelp(
        "Глубина статистики, по которой оптимизатор рассчитывает CPA. Короткое окно быстрее "
        "реагирует, длинное даёт более стабильную оценку.",
        "окно 72 часа использует последние три дня завершённой пятиминутной статистики.",
    ),
}

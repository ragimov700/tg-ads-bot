from decimal import Decimal

import pytest

from app.bot.handlers import EDITABLE, TOGGLE_FIELDS, _validate_value
from app.bot.texts import (
    SETTING_HELP,
    TOGGLE_HELP,
    event_error_label,
    event_status_label,
    event_type_label,
)


class Current:
    min_cpm = Decimal("0.10")
    max_cpm = Decimal("1.00")
    budget_step = Decimal("0.10")
    ad_budget_cap = Decimal("1.00")


def test_interval_input_is_converted_to_seconds_without_display_corruption() -> None:
    spec = EDITABLE["monitor_interval_seconds"]
    assert spec.parse("10") == 600
    assert spec.display(600) == "10 мин"


def test_monitor_interval_must_be_a_multiple_of_five_minutes() -> None:
    with pytest.raises(ValueError):
        _validate_value("monitor_interval_seconds", 360, Current())
    _validate_value("monitor_interval_seconds", 600, Current())


def test_cross_field_budget_validation() -> None:
    with pytest.raises(ValueError):
        _validate_value("budget_step", Decimal(2), Current())


def test_every_automation_control_has_description_and_example() -> None:
    assert SETTING_HELP.keys() == EDITABLE.keys()
    assert TOGGLE_HELP.keys() == TOGGLE_FIELDS.keys()

    for help_text in (*SETTING_HELP.values(), *TOGGLE_HELP.values()):
        rendered = help_text.render()
        assert help_text.description
        assert help_text.example
        assert "<b>Пример:</b>" in rendered


def test_event_labels_are_translated_to_russian() -> None:
    assert event_type_label("budget_refill", "low_budget") == "Бюджет пополнен"
    assert event_type_label("budget_refill", "low_budget", "failed") == "Пополнение бюджета"
    assert event_type_label("worker_error", "TelegramAdsError") == "Ошибка автоматики"
    assert (
        event_type_label("budget_refill_skipped", "performance_data_unavailable")
        == "Пополнение пропущено: статистика CPA недоступна"
    )
    assert event_type_label("settings_change", "budget_step") == "Изменена настройка «Шаг бюджета»"
    assert event_status_label("succeeded") == "успешно"
    assert event_status_label("no_change") == "без изменений"
    assert (
        event_error_label("AD_RESULT_BUDGET_TOO_SMALL")
        == "сумма пополнения слишком мала — увеличьте «Шаг бюджета»"
    )
    assert event_error_label("UNKNOWN_ERROR") is None


def test_unknown_event_values_are_left_readable() -> None:
    assert event_type_label("future_event", "reason") == "future_event"
    assert event_status_label("future_status") == "future_status"

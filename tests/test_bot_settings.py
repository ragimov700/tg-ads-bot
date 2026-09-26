from decimal import Decimal

import pytest

from app.bot.handlers import EDITABLE, _validate_value


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

from decimal import Decimal

from app.automation.rules import (
    BudgetManager,
    CpaOptimizer,
    DecisionKind,
    FraudGuard,
    PerformanceSummary,
    RuleSettings,
    SnapshotMetrics,
    round_cpm,
)


def settings() -> RuleSettings:
    return RuleSettings(
        target_cpa=Decimal("0.25"),
        min_cpm=Decimal("0.10"),
        max_cpm=Decimal("1.00"),
        budget_step=Decimal("0.10"),
        ad_budget_cap=Decimal("1.00"),
        account_reserve=Decimal("1.00"),
        refill_threshold=Decimal("0.02"),
        fraud_min_views=20_000,
        fraud_min_spend=Decimal("0.50"),
        min_actions_increase=3,
        min_sample_multiplier=Decimal(3),
        stop_cpa_multiplier=Decimal(2),
        stop_spend_multiplier=Decimal(4),
    )


def test_fraud_requires_all_three_thresholds() -> None:
    guard = FraudGuard()
    result = guard.evaluate(
        SnapshotMetrics(10_000, 5, Decimal("0.20")),
        SnapshotMetrics(30_000, 5, Decimal("0.70")),
        settings(),
    )
    assert result.kind == DecisionKind.FRAUD_PAUSE

    with_action = guard.evaluate(
        SnapshotMetrics(10_000, 5, Decimal("0.20")),
        SnapshotMetrics(30_000, 6, Decimal("0.70")),
        settings(),
    )
    assert with_action.kind == DecisionKind.NO_CHANGE


def test_optimizer_needs_three_actions_before_increasing_cpm() -> None:
    optimizer = CpaOptimizer()
    result = optimizer.evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.30"), actions=2),
        settings=settings(),
        previous_was_warning=False,
    )
    assert result.kind == DecisionKind.INSUFFICIENT_SAMPLE

    result = optimizer.evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.30"), actions=3),
        settings=settings(),
        previous_was_warning=False,
    )
    assert result.kind == DecisionKind.CPM_INCREASE
    assert result.new_value == Decimal("0.21")


def test_optimizer_decreases_cpm_and_respects_lower_bound() -> None:
    optimizer = CpaOptimizer()
    result = optimizer.evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.90"), actions=3),
        settings=settings(),
        previous_was_warning=False,
    )
    assert result.kind == DecisionKind.CPM_DECREASE
    assert result.new_value == Decimal("0.19")

    result = optimizer.evaluate(
        current_cpm=Decimal("0.10"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.90"), actions=3),
        settings=settings(),
        previous_was_warning=False,
    )
    assert result.kind == DecisionKind.NO_CHANGE


def test_performance_pause_requires_two_consecutive_checks() -> None:
    optimizer = CpaOptimizer()
    performance = PerformanceSummary(spend=Decimal("1.00"), actions=1)
    first = optimizer.evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=performance,
        settings=settings(),
        previous_was_warning=False,
    )
    second = optimizer.evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=performance,
        settings=settings(),
        previous_was_warning=True,
    )
    assert first.kind == DecisionKind.PERFORMANCE_WARNING
    assert second.kind == DecisionKind.PAUSE_PERFORMANCE


def test_zero_actions_uses_stop_spend_threshold() -> None:
    result = CpaOptimizer().evaluate(
        current_cpm=Decimal("0.20"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("1.00"), actions=0),
        settings=settings(),
        previous_was_warning=False,
    )
    assert result.kind == DecisionKind.PERFORMANCE_WARNING


def test_budget_refill_respects_cap_reserve_and_bad_cpa() -> None:
    manager = BudgetManager()
    good = manager.evaluate(
        spent_budget=Decimal("0.50"),
        remaining_budget=Decimal("0.01"),
        account_balance=Decimal("2.00"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.50"), actions=3),
        settings=settings(),
    )
    assert good.kind == DecisionKind.BUDGET_REFILL
    assert good.new_value == Decimal("0.10000")

    cap = manager.evaluate(
        spent_budget=Decimal("0.99"),
        remaining_budget=Decimal("0.01"),
        account_balance=Decimal("2.00"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.50"), actions=3),
        settings=settings(),
    )
    assert cap.kind == DecisionKind.BUDGET_CAP_REACHED

    reserve = manager.evaluate(
        spent_budget=Decimal("0.50"),
        remaining_budget=Decimal("0.01"),
        account_balance=Decimal("1.05"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.50"), actions=3),
        settings=settings(),
    )
    assert reserve.kind == DecisionKind.BUDGET_BLOCKED

    bad = manager.evaluate(
        spent_budget=Decimal("0.50"),
        remaining_budget=Decimal("0.01"),
        account_balance=Decimal("2.00"),
        currency="TON",
        performance=PerformanceSummary(spend=Decimal("0.90"), actions=2),
        settings=settings(),
    )
    assert bad.kind == DecisionKind.BUDGET_BLOCKED


def test_xtr_cpm_rounds_to_integer() -> None:
    assert round_cpm(Decimal("1.6"), "XTR") == Decimal(2)

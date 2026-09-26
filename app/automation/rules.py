"""Pure, deterministic decisions used by the worker."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

ZERO = Decimal(0)


class DecisionKind(StrEnum):
    NO_CHANGE = "no_change"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    PERFORMANCE_WARNING = "performance_warning"
    PAUSE_PERFORMANCE = "pause_performance"
    CPM_INCREASE = "cpm_increase"
    CPM_DECREASE = "cpm_decrease"
    FRAUD_PAUSE = "fraud_pause"
    BUDGET_REFILL = "budget_refill"
    BUDGET_BLOCKED = "budget_blocked"
    BUDGET_CAP_REACHED = "budget_cap_reached"


@dataclass(frozen=True, slots=True)
class RuleSettings:
    target_cpa: Decimal
    min_cpm: Decimal
    max_cpm: Decimal
    budget_step: Decimal
    ad_budget_cap: Decimal
    account_reserve: Decimal
    refill_threshold: Decimal
    fraud_min_views: int
    fraud_min_spend: Decimal
    min_actions_increase: int
    min_sample_multiplier: Decimal
    stop_cpa_multiplier: Decimal
    stop_spend_multiplier: Decimal


@dataclass(frozen=True, slots=True)
class SnapshotMetrics:
    views: int
    actions: int
    spent: Decimal


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    spend: Decimal
    actions: int
    views: int = 0

    @property
    def cpa(self) -> Decimal | None:
        return self.spend / self.actions if self.actions else None


@dataclass(frozen=True, slots=True)
class RuleDecision:
    kind: DecisionKind
    reason: str
    new_value: Decimal | None = None
    metrics: dict[str, object] | None = None


def cpm_quantum(currency: str) -> Decimal:
    return Decimal(1) if currency == "XTR" else Decimal("0.01")


def amount_quantum(currency: str) -> Decimal:
    return Decimal("0.001") if currency == "XTR" else Decimal("0.00001")


def round_cpm(value: Decimal, currency: str) -> Decimal:
    return value.quantize(cpm_quantum(currency), rounding=ROUND_HALF_UP)


def round_amount(value: Decimal, currency: str) -> Decimal:
    return value.quantize(amount_quantum(currency), rounding=ROUND_HALF_UP)


class FraudGuard:
    def evaluate(
        self,
        previous: SnapshotMetrics | None,
        current: SnapshotMetrics,
        settings: RuleSettings,
    ) -> RuleDecision:
        if previous is None:
            return RuleDecision(DecisionKind.NO_CHANGE, "fraud_window_not_ready")
        delta_views = current.views - previous.views
        delta_actions = current.actions - previous.actions
        delta_spend = current.spent - previous.spent
        metrics = {
            "delta_views": delta_views,
            "delta_actions": delta_actions,
            "delta_spend": str(delta_spend),
        }
        if min(delta_views, delta_actions) < 0 or delta_spend < ZERO:
            return RuleDecision(DecisionKind.NO_CHANGE, "counters_were_reset", metrics=metrics)
        if (
            delta_actions == 0
            and delta_views >= settings.fraud_min_views
            and delta_spend >= settings.fraud_min_spend
        ):
            return RuleDecision(
                DecisionKind.FRAUD_PAUSE,
                "suspicious_traffic_without_actions",
                metrics=metrics,
            )
        return RuleDecision(DecisionKind.NO_CHANGE, "fraud_threshold_not_reached", metrics=metrics)


class CpaOptimizer:
    def evaluate(
        self,
        *,
        current_cpm: Decimal,
        currency: str,
        performance: PerformanceSummary,
        settings: RuleSettings,
        previous_was_warning: bool,
    ) -> RuleDecision:
        cpa = performance.cpa
        minimum_spend = settings.target_cpa * settings.min_sample_multiplier
        stop_spend = settings.target_cpa * settings.stop_spend_multiplier
        bad_enough_to_stop = performance.spend >= stop_spend and (
            performance.actions == 0
            or (cpa is not None and cpa > settings.target_cpa * settings.stop_cpa_multiplier)
        )
        metrics = {
            "spend": str(performance.spend),
            "actions": performance.actions,
            "views": performance.views,
            "cpa": str(cpa) if cpa is not None else None,
            "target_cpa": str(settings.target_cpa),
        }
        if bad_enough_to_stop:
            if previous_was_warning:
                return RuleDecision(
                    DecisionKind.PAUSE_PERFORMANCE,
                    "bad_cpa_confirmed_twice",
                    metrics=metrics,
                )
            return RuleDecision(
                DecisionKind.PERFORMANCE_WARNING,
                "bad_cpa_requires_confirmation",
                metrics=metrics,
            )

        sample_sufficient = (
            performance.actions >= settings.min_actions_increase
            or performance.spend >= minimum_spend
        )
        if not sample_sufficient or cpa is None:
            return RuleDecision(
                DecisionKind.INSUFFICIENT_SAMPLE,
                "optimizer_sample_is_too_small",
                metrics=metrics,
            )

        ratio = cpa / settings.target_cpa
        if ratio < Decimal("0.8"):
            if performance.actions < settings.min_actions_increase:
                return RuleDecision(
                    DecisionKind.INSUFFICIENT_SAMPLE,
                    "cpm_increase_requires_more_actions",
                    metrics=metrics,
                )
            desired = current_cpm * Decimal("1.05")
            kind = DecisionKind.CPM_INCREASE
        elif ratio <= Decimal("1.1"):
            return RuleDecision(DecisionKind.NO_CHANGE, "cpa_in_target_band", metrics=metrics)
        elif ratio <= Decimal("1.4"):
            desired = current_cpm * Decimal("0.95")
            kind = DecisionKind.CPM_DECREASE
        else:
            desired = current_cpm * Decimal("0.90")
            kind = DecisionKind.CPM_DECREASE

        desired = max(settings.min_cpm, min(settings.max_cpm, desired))
        desired = round_cpm(desired, currency)
        if desired == current_cpm:
            return RuleDecision(DecisionKind.NO_CHANGE, "cpm_limit_or_rounding", metrics=metrics)
        return RuleDecision(kind, "cpa_outside_target_band", new_value=desired, metrics=metrics)


class BudgetManager:
    def evaluate(
        self,
        *,
        spent_budget: Decimal,
        remaining_budget: Decimal,
        account_balance: Decimal,
        currency: str,
        performance: PerformanceSummary | None,
        settings: RuleSettings,
    ) -> RuleDecision:
        if remaining_budget > settings.refill_threshold:
            return RuleDecision(DecisionKind.NO_CHANGE, "budget_is_sufficient")
        allocated = spent_budget + remaining_budget
        metrics = {
            "spent_budget": str(spent_budget),
            "remaining_budget": str(remaining_budget),
            "allocated_budget": str(allocated),
            "account_balance": str(account_balance),
        }
        capacity = settings.ad_budget_cap - allocated
        if capacity <= ZERO:
            return RuleDecision(
                DecisionKind.BUDGET_CAP_REACHED,
                "ad_budget_cap_reached",
                metrics=metrics,
            )
        if performance is None:
            return RuleDecision(
                DecisionKind.BUDGET_BLOCKED,
                "performance_data_unavailable",
                metrics=metrics,
            )

        sample_sufficient = (
            performance.actions >= settings.min_actions_increase
            or performance.spend >= settings.target_cpa * settings.min_sample_multiplier
        )
        if sample_sufficient and (
            performance.cpa is None or performance.cpa > settings.target_cpa * Decimal("1.1")
        ):
            return RuleDecision(
                DecisionKind.BUDGET_BLOCKED,
                "cpa_is_not_acceptable",
                metrics=metrics,
            )

        amount = round_amount(min(settings.budget_step, capacity), currency)
        if amount <= ZERO or account_balance - amount < settings.account_reserve:
            return RuleDecision(
                DecisionKind.BUDGET_BLOCKED,
                "account_reserve_would_be_violated",
                metrics=metrics,
            )
        return RuleDecision(
            DecisionKind.BUDGET_REFILL,
            "low_budget_and_acceptable_performance",
            new_value=amount,
            metrics=metrics,
        )

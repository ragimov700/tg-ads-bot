"""CPA and aggregate metric helpers."""

from decimal import Decimal

from app.telegram_ads.schemas import AdStatItem


def calculate_cpa(spend: Decimal, actions: int) -> Decimal | None:
    return spend / actions if actions else None


def aggregate(rows: list[AdStatItem], currency: str) -> tuple[Decimal, int, int]:
    if any(row.currency != currency for row in rows):
        raise ValueError("Statistics contain more than one currency")
    return (
        sum((row.spent_budget for row in rows), Decimal(0)),
        sum(row.actions for row in rows),
        sum(row.views for row in rows),
    )

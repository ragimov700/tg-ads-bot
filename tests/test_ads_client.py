import json

import pytest

from app.telegram_ads.client import (
    TelegramAdsClient,
    _api_error_code,
    _is_retryable_api_error,
)


def ad(ad_id: int) -> dict[str, object]:
    return {
        "ad_id": ad_id,
        "currency": "TON",
        "cpm": "0.20",
        "spent_budget": "0.10",
        "remaining_budget": "0.10",
        "status": "active",
    }


class StubClient(TelegramAdsClient):
    def __init__(self) -> None:
        super().__init__("secret", "account")
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def _request(self, method, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "getAdsList":
            if "offset" not in params:
                return {"total_count": 2, "ads": [ad(1)], "next_offset": "next"}
            return {"total_count": 2, "ads": [ad(2)]}
        if method in {"getAdStats", "getAccountStats"}:
            return []
        if method == "getAdsById":
            ids = json.loads(params["ad_ids"])
            return [ad(item) for item in ids]
        raise AssertionError(method)


@pytest.mark.asyncio
async def test_ads_list_follows_string_offsets() -> None:
    client = StubClient()
    result = await client.get_ads()
    assert [item.ad_id for item in result] == [1, 2]
    assert client.calls[1][1]["offset"] == "next"


@pytest.mark.asyncio
async def test_stats_period_is_split_at_one_thousand_intervals() -> None:
    client = StubClient()
    await client.get_account_stats(0, 300 * 2501, 300)
    calls = [params for method, params in client.calls if method == "getAccountStats"]
    assert [(item["from_time"], item["to_time"]) for item in calls] == [
        (0, 300_000),
        (300_000, 600_000),
        (600_000, 750_300),
    ]


@pytest.mark.asyncio
async def test_get_ads_by_id_serializes_the_list() -> None:
    client = StubClient()
    result = await client.get_ads_by_id([10, 11])
    assert [item.ad_id for item in result] == [10, 11]
    assert client.calls[0][1]["ad_ids"] == "[10, 11]"


def test_api_error_code_keeps_only_safe_machine_code() -> None:
    assert _api_error_code({"ok": False, "error": "RATE_LIMIT_EXCEEDED"}) == ("RATE_LIMIT_EXCEEDED")
    assert (
        _api_error_code(
            {"ok": False, "error": {"code": "AUTH_KEY_INVALID", "message": "secret detail"}}
        )
        == "AUTH_KEY_INVALID"
    )


def test_transient_api_error_codes_are_retryable() -> None:
    assert _is_retryable_api_error("RATE_LIMIT_EXCEEDED")
    assert _is_retryable_api_error("FLOOD_WAIT_10")
    assert not _is_retryable_api_error("AUTH_KEY_INVALID")

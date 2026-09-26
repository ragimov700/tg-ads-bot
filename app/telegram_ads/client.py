"""Resilient typed client for https://promoteapi.telegram.org."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, Self

import aiohttp
from pydantic import TypeAdapter, ValidationError

from app.telegram_ads.exceptions import TelegramAdsError
from app.telegram_ads.schemas import Account, Ad, AdList, AdStatItem

LOGGER = logging.getLogger(__name__)
ACCOUNTS = TypeAdapter(list[Account])
ADS = TypeAdapter(list[Ad])
STATS = TypeAdapter(list[AdStatItem])
RETRYABLE_API_ERRORS = {"INTERNAL_SERVER_ERROR", "IDEMPOTENT_REQUEST_IN_PROGRESS"}


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


class TelegramAdsClient:
    def __init__(
        self,
        token: str,
        account_id: str,
        *,
        base_url: str = "https://promoteapi.telegram.org",
        timeout_seconds: float = 20,
        retry_delays: tuple[float, ...] = (0.5, 1.5, 4.0),
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.account_id = account_id
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._retry_delays = retry_delays
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        http_method: Literal["GET", "POST"] = "GET",
        idempotency_key: str | None = None,
    ) -> object:
        if self._session is None:
            raise RuntimeError("TelegramAdsClient must be used as an async context manager")
        payload = {key: _json_value(value) for key, value in (params or {}).items()}
        retryable_request = http_method == "GET" or idempotency_key is not None
        attempts = len(self._retry_delays) + 1 if retryable_request else 1
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": "tg-ads-automation/0.1",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        last_error: TelegramAdsError | None = None
        for attempt in range(attempts):
            try:
                async with self._session.request(
                    http_method,
                    f"{self._base_url}/{method}",
                    params=payload if http_method == "GET" else None,
                    json=payload if http_method == "POST" else None,
                    headers=headers,
                ) as response:
                    raw = await response.text()
                    try:
                        body = json.loads(raw, parse_float=Decimal)
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise TelegramAdsError(
                            "Telegram Ads API returned invalid JSON",
                            retryable=response.status >= 500,
                            ambiguous=http_method == "POST",
                        ) from exc

                    if response.status == 429 or response.status >= 500:
                        raise TelegramAdsError(
                            f"Telegram Ads API HTTP {response.status}",
                            retryable=True,
                            ambiguous=http_method == "POST",
                        )
                    if not isinstance(body, dict) or body.get("ok") is not True:
                        code = (
                            str(body.get("error", "UNKNOWN_ERROR"))
                            if isinstance(body, dict)
                            else None
                        )
                        raise TelegramAdsError(
                            "Telegram Ads API rejected the request",
                            code=code,
                            retryable=code in RETRYABLE_API_ERRORS,
                            ambiguous=code == "IDEMPOTENT_REQUEST_IN_PROGRESS",
                        )
                    if "result" not in body:
                        raise TelegramAdsError("Telegram Ads API response has no result")
                    return body["result"]
            except TelegramAdsError as exc:
                last_error = exc
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = TelegramAdsError(
                    "Telegram Ads API is unavailable",
                    retryable=True,
                    ambiguous=http_method == "POST",
                )
                last_error.__cause__ = exc

            if not retryable_request or not last_error.retryable or attempt == attempts - 1:
                raise last_error
            delay = self._retry_delays[attempt]
            LOGGER.warning("Retrying Telegram Ads method %s after a transient failure", method)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _validate(model, value: object, label: str):
        try:
            return (
                model.validate_python(value)
                if hasattr(model, "validate_python")
                else model.model_validate(value)
            )
        except ValidationError as exc:
            raise TelegramAdsError(f"Invalid {label} response from Telegram Ads API") from exc

    async def get_current_account(self) -> Account:
        result = await self._request("getCurrentAccount")
        return self._validate(Account, result, "account")

    async def get_account(self) -> Account:
        current = await self.get_current_account()
        if current.account_id == self.account_id:
            return current
        result = await self._request(
            "getAccountsById", {"account_ids": json.dumps([self.account_id])}
        )
        accounts = self._validate(ACCOUNTS, result, "accounts")
        if not accounts:
            raise TelegramAdsError("Configured Telegram Ads account is unavailable")
        return accounts[0]

    async def get_ads(self) -> list[Ad]:
        ads: list[Ad] = []
        offset: str | None = None
        seen_offsets: set[str] = set()
        while True:
            params: dict[str, object] = {
                "account_id": self.account_id,
                "limit": 100,
            }
            if offset:
                params["offset"] = offset
            result = await self._request("getAdsList", params)
            page = self._validate(AdList, result, "ads list")
            ads.extend(page.ads)
            offset = page.next_offset
            if not offset:
                break
            if offset in seen_offsets:
                raise TelegramAdsError("Telegram Ads API returned a repeated pagination offset")
            seen_offsets.add(offset)
        return ads

    async def get_ads_by_id(self, ad_ids: list[int]) -> list[Ad]:
        if not ad_ids:
            return []
        result = await self._request(
            "getAdsById",
            {"account_id": self.account_id, "ad_ids": json.dumps(ad_ids)},
        )
        return self._validate(ADS, result, "ads")

    async def _get_stats(
        self,
        method: str,
        from_time: int,
        to_time: int,
        interval: int,
        *,
        ad_id: int | None = None,
    ) -> list[AdStatItem]:
        if interval not in {300, 86400}:
            raise ValueError("Telegram Ads statistics interval must be 300 or 86400")
        if to_time <= from_time:
            return []
        result: list[AdStatItem] = []
        cursor = from_time
        max_span = interval * 1000
        while cursor < to_time:
            chunk_end = min(cursor + max_span, to_time)
            params: dict[str, object] = {
                "account_id": self.account_id,
                "from_time": cursor,
                "to_time": chunk_end,
                "interval": interval,
            }
            if ad_id is not None:
                params["ad_id"] = ad_id
            raw = await self._request(method, params)
            result.extend(self._validate(STATS, raw, "statistics"))
            cursor = chunk_end
        return result

    async def get_ad_stats(
        self, ad_id: int, from_time: int, to_time: int, interval: int = 300
    ) -> list[AdStatItem]:
        return await self._get_stats("getAdStats", from_time, to_time, interval, ad_id=ad_id)

    async def get_account_stats(
        self, from_time: int, to_time: int, interval: int = 300
    ) -> list[AdStatItem]:
        return await self._get_stats("getAccountStats", from_time, to_time, interval)

    async def edit_ad(
        self,
        ad_id: int,
        *,
        cpm: Decimal | None = None,
        is_paused: bool | None = None,
    ) -> Ad:
        params: dict[str, object] = {"account_id": self.account_id, "ad_id": ad_id}
        if cpm is not None:
            params["cpm"] = cpm
        if is_paused is not None:
            params["is_paused"] = is_paused
        result = await self._request("editAd", params, http_method="POST")
        return self._validate(Ad, result, "edited ad")

    async def increase_ad_budget(self, ad_id: int, amount: Decimal, idempotency_key: str) -> Ad:
        result = await self._request(
            "increaseAdBudget",
            {"account_id": self.account_id, "ad_id": ad_id, "amount": amount},
            http_method="POST",
            idempotency_key=idempotency_key,
        )
        return self._validate(Ad, result, "budget operation")

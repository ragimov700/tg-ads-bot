"""Environment configuration without logging secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    admin_chat_id: int
    ads_api_token: str
    ads_account_id: str
    database_url: URL
    ads_api_base_url: str = "https://promoteapi.telegram.org"
    log_level: str = "INFO"


def load_database_url() -> URL:
    explicit = os.environ.get("DATABASE_URL", "").strip()
    if explicit:
        url = make_url(explicit)
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+asyncpg")
        return url

    password = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("POSTGRES_PASSWORD is required")
    try:
        port = int(os.environ.get("DB_PORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError("DB_PORT must be between 1 and 65535") from exc
    return URL.create(
        "postgresql+asyncpg",
        username=os.environ.get("POSTGRES_USER", "tg_ads_bot"),
        password=password,
        host=os.environ.get("DB_HOST", "localhost"),
        port=port,
        database=os.environ.get("POSTGRES_DB", "tg_ads_bot"),
    )


def load_config() -> Config:
    required = {
        "BOT_TOKEN": os.environ.get("BOT_TOKEN", "").strip(),
        "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "ADS_API_TOKEN": os.environ.get("ADS_API_TOKEN", "").strip(),
        "ADS_ACCOUNT_ID": os.environ.get("ADS_ACCOUNT_ID", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")
    try:
        admin_chat_id = int(required["TELEGRAM_CHAT_ID"])
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_CHAT_ID must be an integer") from exc
    return Config(
        bot_token=required["BOT_TOKEN"],
        admin_chat_id=admin_chat_id,
        ads_api_token=required["ADS_API_TOKEN"],
        ads_account_id=required["ADS_ACCOUNT_ID"],
        database_url=load_database_url(),
        ads_api_base_url=os.environ.get(
            "ADS_API_BASE_URL", "https://promoteapi.telegram.org"
        ).rstrip("/"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )

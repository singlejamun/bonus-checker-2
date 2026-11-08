"""Settings loaded from environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    prefix: str
    database_path: str
    timezone: ZoneInfo
    week_start_hour: int
    default_price: int
    log_level: str


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "put-your-token-here":
        raise SystemExit("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in.")
    tz_name = os.getenv("TIMEZONE", "Asia/Kolkata").strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Invalid TIMEZONE '{tz_name}': {exc}") from exc
    return Config(
        token=token,
        prefix=os.getenv("PREFIX", "??"),
        database_path=os.getenv("DATABASE_PATH", "data/killbot.db"),
        timezone=tz,
        week_start_hour=int(os.getenv("WEEK_START_HOUR", "8")),
        default_price=int(os.getenv("DEFAULT_PRICE", "50000")),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )

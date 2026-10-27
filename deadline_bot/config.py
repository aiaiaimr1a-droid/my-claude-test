import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    telegram_token: str
    owner_id: int | None
    tz: ZoneInfo
    digest_time: time
    soon_hours: int
    db_path: str
    model: str


def _parse_time(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


def load_config() -> Config:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Не задано TELEGRAM_BOT_TOKEN (див. .env.example)")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Не задано ANTHROPIC_API_KEY (див. .env.example)")

    owner = os.environ.get("OWNER_ID", "").strip()
    return Config(
        telegram_token=token,
        owner_id=int(owner) if owner else None,
        tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Kyiv")),
        digest_time=_parse_time(os.environ.get("DIGEST_TIME", "09:00")),
        soon_hours=int(os.environ.get("SOON_HOURS", "3")),
        db_path=os.environ.get("DB_PATH", "tasks.db"),
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
    )

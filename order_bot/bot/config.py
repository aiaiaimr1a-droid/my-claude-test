import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _chat_ref(value: str) -> int | str:
    """'-100123' -> int, '@group' / 'group' -> str."""
    try:
        return int(value)
    except ValueError:
        return value


def _optional_int(value: str | None) -> int | None:
    return int(value) if value else None


@dataclass(frozen=True)
class Config:
    bot_token: str
    paid_channel_id: int
    contact_limit: int
    admin_ids: frozenset[int]
    subscribe_url: str
    free_channel_id: int | None
    free_delay_minutes: int
    db_path: str
    dedup_hours: int
    api_id: int | None
    api_hash: str | None
    userbot_session: str
    userbot_chats: list[int | str]
    userbot_public_chats: list[str]
    userbot_poll_seconds: int

    @property
    def own_channels(self) -> set[int]:
        return {c for c in (self.paid_channel_id, self.free_channel_id) if c}

    @property
    def userbot_enabled(self) -> bool:
        return bool(self.api_id and self.api_hash)


def load_config() -> Config:
    load_dotenv()
    env = os.environ
    return Config(
        bot_token=env["BOT_TOKEN"],
        paid_channel_id=int(env["PAID_CHANNEL_ID"]),
        contact_limit=int(env.get("CONTACT_LIMIT", "5")),
        admin_ids=frozenset(int(x) for x in _list(env.get("ADMIN_IDS", ""))),
        subscribe_url=env.get("SUBSCRIBE_URL", ""),
        free_channel_id=_optional_int(env.get("FREE_CHANNEL_ID")),
        free_delay_minutes=int(env.get("FREE_DELAY_MINUTES", "240")),
        db_path=env.get("DB_PATH", "orders.sqlite3"),
        dedup_hours=int(env.get("DEDUP_HOURS", "72")),
        api_id=_optional_int(env.get("API_ID")),
        api_hash=env.get("API_HASH") or None,
        userbot_session=env.get("USERBOT_SESSION", "userbot"),
        userbot_chats=[_chat_ref(x) for x in _list(env.get("USERBOT_CHATS", ""))],
        userbot_public_chats=_list(env.get("USERBOT_PUBLIC_CHATS", "")),
        userbot_poll_seconds=int(env.get("USERBOT_POLL_SECONDS", "180")),
    )

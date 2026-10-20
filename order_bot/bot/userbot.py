"""Источник №2: юзербот на обычном аккаунте Telegram (Telethon).

Читает группы, где аккаунт состоит участником, но куда нельзя добавить бота
(чужие студенческие чаты). Сам ничего не пишет — публикует только основной бот,
поэтому риск блокировки аккаунта минимальный. Всё равно используйте отдельный номер.

Первый запуск — интерактивный: Telethon спросит номер телефона и код из Telegram,
затем сохранит сессию в файл <USERBOT_SESSION>.session.
"""

import logging

from telethon import TelegramClient, events
from telethon.tl.types import User

from .config import Config
from .models import RawMessage
from .publisher import Publisher

log = logging.getLogger(__name__)


def _link(chat, message_id: int) -> str | None:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if getattr(chat, "megagroup", False) or getattr(chat, "broadcast", False):
        return f"https://t.me/c/{chat.id}/{message_id}"
    return None


async def run_userbot(cfg: Config, publisher: Publisher) -> None:
    client = TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash)
    explicit = bool(cfg.userbot_chats)

    @client.on(events.NewMessage(chats=cfg.userbot_chats or None, incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        # Без явного списка слушаем только группы, чтобы не тащить личку и каналы
        if not explicit and not event.is_group:
            return
        if not event.raw_text:
            return
        sender = await event.get_sender()
        if isinstance(sender, User) and sender.bot:
            return
        chat = await event.get_chat()
        name = " ".join(
            p for p in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if p
        ) or getattr(sender, "title", None) or "—"
        await publisher.handle(
            RawMessage(
                chat_id=event.chat_id,
                chat_title=getattr(chat, "title", None) or "—",
                message_id=event.id,
                link=_link(chat, event.id),
                author_id=sender.id if isinstance(sender, User) else None,
                author_username=getattr(sender, "username", None),
                author_name=name,
                text=event.raw_text,
            )
        )

    await client.start()
    me = await client.get_me()
    log.info("userbot started as %s", me.username or me.id)
    await client.run_until_disconnected()

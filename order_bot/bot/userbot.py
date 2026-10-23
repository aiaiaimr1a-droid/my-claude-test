"""Источник №2: юзербот на обычном аккаунте Telegram (Telethon).

Два режима, работают одновременно:
  * группы, где аккаунт состоит участником, — сообщения приходят сразу;
  * публичные группы из USERBOT_PUBLIC_CHATS — читаются БЕЗ вступления:
    раз в USERBOT_POLL_SECONDS бот забирает новые сообщения из истории.

Сам юзербот ничего не пишет — публикует только основной бот, поэтому риск
блокировки аккаунта минимальный. Всё равно используйте отдельный номер.

Первый запуск — интерактивный: Telethon спросит номер телефона и код из Telegram,
затем сохранит сессию в файл <USERBOT_SESSION>.session.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, events, utils
from telethon.errors import FloodWaitError
from telethon.tl.custom import Message
from telethon.tl.types import User

from .config import Config
from .models import RawMessage
from .publisher import Publisher

log = logging.getLogger(__name__)

# При первом опросе публичной группы берём сообщения не старше этого
FIRST_POLL_WINDOW = timedelta(hours=2)


def _link(chat, message_id: int) -> str | None:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if getattr(chat, "megagroup", False) or getattr(chat, "broadcast", False):
        return f"https://t.me/c/{chat.id}/{message_id}"
    return None


async def _publish(publisher: Publisher, message: Message, chat) -> None:
    if not message.raw_text:
        return
    sender = await message.get_sender()
    if isinstance(sender, User) and sender.bot:
        return
    name = " ".join(
        p for p in (getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if p
    ) or getattr(sender, "title", None) or "—"
    await publisher.handle(
        RawMessage(
            chat_id=message.chat_id,
            chat_title=getattr(chat, "title", None) or "—",
            message_id=message.id,
            link=_link(chat, message.id),
            author_id=sender.id if isinstance(sender, User) else None,
            author_username=getattr(sender, "username", None),
            author_name=name,
            text=message.raw_text,
        )
    )


async def _cached_peer(client: TelegramClient, ref: str):
    """Чат из памяти сессии — без запроса к Telegram. None, если его там нет."""
    try:
        return await utils.maybe_async(client.session.get_input_entity(ref))
    except ValueError:
        return None


async def _poll_public_chats(client: TelegramClient, cfg: Config, publisher: Publisher) -> None:
    """Читает публичные группы без вступления.

    Поиск чата по @имени у Telegram ограничен суточным лимитом, поэтому:
      * get_input_entity берёт чат из памяти сессии (find_groups уже всё туда
        записал) и идёт в сеть, только если чата там нет;
      * при FloodWait бот не засыпает целиком, а откладывает только то,
        что упёрлось в лимит, и продолжает читать остальные группы.
    """
    peers = {}  # @имя -> InputPeer
    last_ids: dict[str, int] = {}
    skip_until: dict[str, float] = {}  # группы, которые временно не читаем
    resolve_paused_until = 0.0  # пока не истечёт — новых имён не ищем
    while True:
        read = 0
        for ref in cfg.userbot_public_chats:
            now = time.time()
            if skip_until.get(ref, 0) > now:
                continue

            if ref not in peers:
                cached = await _cached_peer(client, ref)
                if cached is not None:
                    peers[ref] = cached
                elif resolve_paused_until > now:
                    continue
                else:
                    try:
                        peers[ref] = await client.get_input_entity(ref)
                    except FloodWaitError as e:
                        resolve_paused_until = now + e.seconds
                        log.warning(
                            "username lookup limit reached, new chats paused for %.1f h; "
                            "already known chats keep working",
                            e.seconds / 3600,
                        )
                        continue
                    except Exception as e:  # приватная, удалённая, опечатка в имени
                        log.warning("cannot find %s: %s", ref, e)
                        skip_until[ref] = now + 24 * 3600
                        continue

            min_id = last_ids.get(ref, 0)
            try:
                messages = await client.get_messages(peers[ref], limit=50, min_id=min_id)
            except FloodWaitError as e:
                log.warning("flood wait %ss on %s, skipping it for now", e.seconds, ref)
                skip_until[ref] = now + e.seconds
                continue
            except Exception as e:
                log.warning("cannot read %s: %s", ref, e)
                skip_until[ref] = now + 3600
                continue

            since = datetime.now(timezone.utc) - FIRST_POLL_WINDOW
            for message in reversed(messages):
                if min_id == 0 and message.date < since:
                    continue
                await _publish(publisher, message, message.chat or await message.get_chat())
            if messages:
                last_ids[ref] = max(m.id for m in messages)
            read += 1
            await asyncio.sleep(2)
        waiting = len(cfg.userbot_public_chats) - read
        log.info(
            "public chats checked: %d of %d%s",
            read,
            len(cfg.userbot_public_chats),
            f" ({waiting} waiting: limits or errors)" if waiting else "",
        )
        await asyncio.sleep(cfg.userbot_poll_seconds)


async def run_userbot(cfg: Config, publisher: Publisher) -> None:
    client = TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash)
    explicit = bool(cfg.userbot_chats)

    @client.on(events.NewMessage(chats=cfg.userbot_chats or None, incoming=True))
    async def on_message(event: events.NewMessage.Event) -> None:
        # Без явного списка слушаем только группы, чтобы не тащить личку и каналы
        if not explicit and not event.is_group:
            return
        await _publish(publisher, event.message, await event.get_chat())

    await client.start()
    me = await client.get_me()
    log.info("userbot started as %s", me.username or me.id)
    if cfg.userbot_public_chats:
        log.info("polling %d public chats", len(cfg.userbot_public_chats))
        await asyncio.gather(
            client.run_until_disconnected(), _poll_public_chats(client, cfg, publisher)
        )
    else:
        await client.run_until_disconnected()

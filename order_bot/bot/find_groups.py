"""Поиск публичных групп и каналов по ключевым словам через аккаунт юзербота.

    python -m bot.find_groups "КПІ" "студенти" "курсові"

Печатает найденные чаты и готовую строку USERBOT_PUBLIC_CHATS для .env.
Перед запуском остановите основного бота (Ctrl+C): они делят файл сессии.
"""

import asyncio
import sys

from telethon import TelegramClient, functions
from telethon.tl.types import Channel

from .config import load_config


async def main(queries: list[str]) -> None:
    cfg = load_config()
    if not cfg.userbot_enabled:
        sys.exit("Fill API_ID and API_HASH in .env first.")

    found: dict[str, Channel] = {}
    async with TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash) as client:
        for query in queries:
            result = await client(functions.contacts.SearchRequest(q=query, limit=50))
            for chat in result.chats:
                if isinstance(chat, Channel) and chat.username:
                    found[chat.username] = chat
            await asyncio.sleep(2)

    groups = sorted(found.values(), key=lambda c: -(c.participants_count or 0))
    for chat in groups:
        kind = "group" if chat.megagroup else "channel"
        members = chat.participants_count or "?"
        print(f"{members:>8}  {kind:7}  @{chat.username:<32} {chat.title}")
    print(f"\nFound: {len(groups)}. Groups only, for .env:\n")
    print("USERBOT_PUBLIC_CHATS=" + ",".join("@" + c.username for c in groups if c.megagroup))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python -m bot.find_groups "keyword" ["keyword" ...]')
    asyncio.run(main(sys.argv[1:]))

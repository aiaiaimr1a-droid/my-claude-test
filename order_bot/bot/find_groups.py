"""Поиск публичных групп и каналов по ключевым словам через аккаунт юзербота.

    python -m bot.find_groups "КПІ" "студенти" "курсові"
    python -m bot.find_groups            # запросы из queries.txt

Печатает найденные чаты и готовую строку USERBOT_PUBLIC_CHATS для .env:
группы, которые уже есть в .env, плюс новые.
Перед запуском остановите основного бота (Ctrl+C): они делят файл сессии.
"""

import asyncio
import sys
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel

from .config import load_config

QUERIES_FILE = Path("queries.txt")


def _load_queries(args: list[str]) -> list[str]:
    if args:
        return args
    if not QUERIES_FILE.exists():
        sys.exit('No queries. Usage: python -m bot.find_groups "keyword" ... or fill queries.txt')
    lines = QUERIES_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


async def main(queries: list[str]) -> None:
    cfg = load_config()
    if not cfg.userbot_enabled:
        sys.exit("Fill API_ID and API_HASH in .env first.")

    found: dict[str, Channel] = {}
    async with TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash) as client:
        for i, query in enumerate(queries, 1):
            print(f"[{i}/{len(queries)}] {query}", file=sys.stderr)
            try:
                result = await client(functions.contacts.SearchRequest(q=query, limit=50))
            except FloodWaitError as e:
                print(f"Telegram asks to wait {e.seconds}s, stopping early.", file=sys.stderr)
                break
            for chat in result.chats:
                if isinstance(chat, Channel) and chat.username:
                    found[chat.username.lower()] = chat
            await asyncio.sleep(3)

    groups = sorted(found.values(), key=lambda c: -(c.participants_count or 0))
    for chat in groups:
        kind = "group" if chat.megagroup else "channel"
        members = chat.participants_count or "?"
        print(f"{members:>8}  {kind:7}  @{chat.username:<32} {chat.title}")

    existing = list(cfg.userbot_public_chats)
    known = {name.lstrip("@").lower() for name in existing}
    new = ["@" + c.username for c in groups if c.megagroup and c.username.lower() not in known]
    print(f"\nFound: {len(groups)}. New groups: {len(new)}. Line for .env (old + new):\n")
    print("USERBOT_PUBLIC_CHATS=" + ",".join(existing + new))


if __name__ == "__main__":
    asyncio.run(main(_load_queries(sys.argv[1:])))

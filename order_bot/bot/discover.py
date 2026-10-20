"""Поиск закрытых студенческих чатов через каналы вузов.

    python -m bot.discover                 # запросы из discover_queries.txt
    python -m bot.discover "профком КНУ"   # или свои

1. Находит публичные каналы и группы по запросам (профкомы, студради,
   «Підслухано», приймальні комісії, факультети).
2. Читает их последние посты и собирает ссылки на чаты: приглашения
   t.me/+..., t.me/joinchat/..., а также публичные t.me/name и @name.
3. По приглашениям узнаёт название и число участников БЕЗ вступления,
   публичные имена — резолвит (с ограничением, у Telegram на это лимит).
4. Сохраняет всё в discovered.csv (открывается в Excel) и печатает строку
   USERBOT_PUBLIC_CHATS с найденными публичными группами.

Вступать в закрытые чаты — вручную и постепенно, по несколько в день.
Перед запуском остановите основного бота (Ctrl+C): они делят файл сессии.
"""

import asyncio
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, functions
from telethon.errors import (
    FloodWaitError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    RPCError,
)
from telethon.tl.types import (
    Channel,
    ChatInvite,
    ChatInviteAlready,
    ChatInvitePeek,
    MessageEntityTextUrl,
)

from .config import load_config

QUERIES_FILE = Path("discover_queries.txt")
OUTPUT_FILE = Path("discovered.csv")

MAX_SEEDS = 60  # сколько каналов-источников просматривать
POSTS_PER_SEED = 400  # сколько последних постов в каждом
MAX_RESOLVE = 60  # сколько публичных имён проверять за запуск (лимит Telegram)
MAX_FLOOD_SLEEP = 60  # дольше этого не ждём — останавливаемся и сохраняем, что есть

INVITE_RE = re.compile(r"(?:t|telegram)\.me/(?:\+|joinchat/)([A-Za-z0-9_-]{10,})", re.IGNORECASE)
PUBLIC_LINK_RE = re.compile(
    r"(?:t|telegram)\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})(?![A-Za-z0-9_])", re.IGNORECASE
)
MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z][A-Za-z0-9_]{4,31})")
NOT_CHATS = {"joinchat", "addstickers", "addemoji", "addlist", "share", "proxy", "socks",
             "iv", "c", "s", "boost", "setlanguage", "login", "confirmphone"}

STUDY_WORDS = (
    "курс", "груп", "факульт", "кафедр", "потік", "поток", "спеціальн", "специальн",
    "студент", "першокурс", "первокурс", "бакалавр", "магістр", "магистр", "абітур",
    "абитур", "гуртож", "общаг", "староста", "сесі", "сесси", "навчан", "учеб", "розклад",
    "расписан", "інститут", "институт", "університет", "университет", "академі", "академи",
    "коледж", "колледж", "university", "student", "faculty", "group", "course",
)


@dataclass
class Found:
    kind: str  # invite / public
    link: str
    title: str = ""
    members: int | None = None
    chat_type: str = ""  # group / channel
    needs_approval: bool = False
    status: str = ""  # ok / expired / already member / not checked
    source: str = ""

    @property
    def is_study(self) -> bool:
        low = self.title.lower()
        return any(w in low for w in STUDY_WORDS)


def extract_links(text: str, urls: list[str]) -> tuple[set[str], set[str]]:
    """Возвращает (хеши приглашений, публичные имена) из текста и ссылок."""
    blob = " ".join([text or "", *urls])
    invites = set(INVITE_RE.findall(blob))
    names = {
        name.lower()
        for name in PUBLIC_LINK_RE.findall(blob) + MENTION_RE.findall(text or "")
        if name.lower() not in NOT_CHATS and not name.lower().endswith("bot")
    }
    return invites, names


def _message_urls(message) -> list[str]:
    urls = []
    for entity, _ in message.get_entities_text():
        if isinstance(entity, MessageEntityTextUrl):
            urls.append(entity.url)
    for row in message.buttons or []:
        for button in row:
            if getattr(button, "url", None):
                urls.append(button.url)
    return urls


def _load_queries(args: list[str]) -> list[str]:
    if args:
        return args
    if not QUERIES_FILE.exists():
        sys.exit('No queries. Usage: python -m bot.discover "query" ... or fill discover_queries.txt')
    lines = QUERIES_FILE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


async def _call(client, request):
    """Запрос с обработкой FloodWait. None — если Telegram просит ждать слишком долго."""
    try:
        return await client(request)
    except FloodWaitError as e:
        if e.seconds > MAX_FLOOD_SLEEP:
            print(f"Telegram asks to wait {e.seconds}s — stopping this step.", file=sys.stderr)
            return None
        await asyncio.sleep(e.seconds + 1)
        return await client(request)


async def main(queries: list[str]) -> None:
    cfg = load_config()
    if not cfg.userbot_enabled:
        sys.exit("Fill API_ID and API_HASH in .env first.")

    async with TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash) as client:
        # 1. Каналы-источники
        seeds: dict[int, Channel] = {}
        for i, query in enumerate(queries, 1):
            print(f"[search {i}/{len(queries)}] {query}", file=sys.stderr)
            result = await _call(client, functions.contacts.SearchRequest(q=query, limit=50))
            if result is None:
                break
            for chat in result.chats:
                if isinstance(chat, Channel) and chat.username:
                    seeds[chat.id] = chat
            await asyncio.sleep(3)
        top_seeds = sorted(seeds.values(), key=lambda c: -(c.participants_count or 0))[:MAX_SEEDS]
        seed_names = {c.username.lower() for c in seeds.values()}

        # 2. Ссылки из постов
        invites: dict[str, str] = {}  # hash -> где нашли
        names: dict[str, str] = {}
        for i, seed in enumerate(top_seeds, 1):
            print(f"[read {i}/{len(top_seeds)}] @{seed.username}", file=sys.stderr)
            try:
                async for message in client.iter_messages(seed, limit=POSTS_PER_SEED):
                    found_invites, found_names = extract_links(
                        message.raw_text, _message_urls(message)
                    )
                    for h in found_invites:
                        invites.setdefault(h, "@" + seed.username)
                    for n in found_names - seed_names:
                        names.setdefault(n, "@" + seed.username)
            except FloodWaitError as e:
                print(f"Telegram asks to wait {e.seconds}s — stopping reading.", file=sys.stderr)
                break
            except RPCError as e:
                print(f"  cannot read @{seed.username}: {e}", file=sys.stderr)
            await asyncio.sleep(1)

        # 3. Проверка приглашений — без вступления
        results: list[Found] = []
        stopped = False
        for i, (h, source) in enumerate(invites.items(), 1):
            print(f"[invite {i}/{len(invites)}]", file=sys.stderr)
            item = Found("invite", f"https://t.me/+{h}", source=source, status="not checked")
            results.append(item)
            if stopped:
                continue
            try:
                info = await _call(client, functions.messages.CheckChatInviteRequest(hash=h))
            except (InviteHashExpiredError, InviteHashInvalidError):
                item.status = "expired"
                continue
            except RPCError as e:
                item.status = f"error: {e.__class__.__name__}"
                continue
            if info is None:
                stopped = True
                continue
            if isinstance(info, ChatInvite):
                item.title = info.title
                item.members = info.participants_count
                item.chat_type = "channel" if info.broadcast else "group"
                item.needs_approval = bool(info.request_needed)
                item.status = "ok"
            elif isinstance(info, (ChatInviteAlready, ChatInvitePeek)):
                item.title = getattr(info.chat, "title", "")
                item.members = getattr(info.chat, "participants_count", None)
                item.chat_type = "channel" if getattr(info.chat, "broadcast", False) else "group"
                item.status = "already member"
            await asyncio.sleep(2)

        # 4. Публичные имена — с ограничением
        for i, (name, source) in enumerate(names.items(), 1):
            item = Found("public", f"https://t.me/{name}", source=source, status="not checked")
            if i > MAX_RESOLVE or stopped:
                results.append(item)
                continue
            print(f"[public {i}/{min(len(names), MAX_RESOLVE)}] @{name}", file=sys.stderr)
            try:
                entity = await client.get_entity(name)
            except FloodWaitError as e:
                print(f"Telegram asks to wait {e.seconds}s — stopping.", file=sys.stderr)
                stopped = True
                results.append(item)
                continue
            except (ValueError, RPCError):
                continue  # не существует или это человек/бот
            if not isinstance(entity, Channel):
                continue
            item.title = entity.title
            item.members = entity.participants_count
            item.chat_type = "group" if entity.megagroup else "channel"
            item.status = "ok"
            results.append(item)
            await asyncio.sleep(3)

    _save(results)
    _report(results, cfg.userbot_public_chats)


def _save(results: list[Found]) -> None:
    rows = sorted(results, key=lambda r: (not r.is_study, r.status != "ok", -(r.members or 0)))
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")  # ";" — чтобы Excel сразу разбил на колонки
        writer.writerow(["study", "kind", "title", "members", "type", "needs approval",
                         "status", "link", "found in"])
        for r in rows:
            writer.writerow(["yes" if r.is_study else "", r.kind, r.title, r.members or "",
                             r.chat_type, "yes" if r.needs_approval else "", r.status,
                             r.link, r.source])


def _report(results: list[Found], existing: list[str]) -> None:
    ok = [r for r in results if r.status == "ok"]
    study_invites = [r for r in ok if r.kind == "invite" and r.is_study and r.chat_type == "group"]
    for r in sorted(study_invites, key=lambda r: -(r.members or 0))[:30]:
        approval = "  (request to join)" if r.needs_approval else ""
        print(f"{r.members or '?':>7}  {r.title[:50]:<50} {r.link}{approval}")

    known = {n.lstrip("@").lower() for n in existing}
    public_groups = [
        "@" + r.link.rsplit("/", 1)[1]
        for r in ok
        if r.kind == "public" and r.chat_type == "group" and r.is_study
        and r.link.rsplit("/", 1)[1].lower() not in known
    ]
    print(
        f"\nInvite links: {sum(r.kind == 'invite' for r in results)}, "
        f"study groups among them: {len(study_invites)}. "
        f"New public study groups: {len(public_groups)}."
    )
    print(f"Full list saved to {OUTPUT_FILE.resolve()}")
    if public_groups:
        print("\nLine for .env (old + new):\n")
        print("USERBOT_PUBLIC_CHATS=" + ",".join(existing + public_groups))


if __name__ == "__main__":
    asyncio.run(main(_load_queries(sys.argv[1:])))

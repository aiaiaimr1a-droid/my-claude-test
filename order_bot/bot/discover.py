"""Поиск закрытых студенческих чатов через каналы вузов.

    python -m bot.discover                 # запросы из discover_queries.txt
    python -m bot.discover "профком КНУ"   # или свои

1. Находит публичные каналы и группы по запросам (профкомы, студради,
   «Підслухано», приймальні комісії, факультети).
2. Читает их последние посты и собирает ссылки на чаты: приглашения
   t.me/+..., t.me/joinchat/..., а также публичные t.me/name и @name.
3. Проверяет ссылки БЕЗ вступления — сначала свежие и те, рядом с которыми
   в посте есть «чат курсу», «група», «факультет».
4. Запоминает результаты в discover_cache.json: недействительные ссылки
   больше не проверяются, рабочие — раз в неделю, а что не успели —
   доделывается при следующем запуске.
5. Сохраняет рабочие ссылки в discovered.csv (открывается в Excel) и печатает
   строку USERBOT_PUBLIC_CHATS с новыми публичными учебными группами.

Вступать в закрытые чаты — вручную и постепенно, по несколько в день.
Перед запуском остановите основного бота (Ctrl+C): они делят файл сессии.
"""

import asyncio
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
CACHE_FILE = Path("discover_cache.json")

MAX_SEEDS = 60  # сколько каналов-источников просматривать
POSTS_PER_SEED = 400  # сколько последних постов в каждом
MAX_CHECKS = 150  # сколько ссылок проверять за один запуск (лимит Telegram)
RECHECK_DAYS = 7  # рабочие ссылки перепроверяем раз в неделю
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

OK_STATUSES = {"ok", "already member"}
FINAL_BAD = {"expired", "not a chat"}


def _is_study(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in STUDY_WORDS)


@dataclass
class Found:
    kind: str  # invite / public
    key: str  # хеш приглашения или имя
    title: str = ""
    members: int | None = None
    chat_type: str = ""  # group / channel
    needs_approval: bool = False
    status: str = "not checked"
    checked_at: float = 0
    source: str = ""
    posted_at: float = 0  # дата самого свежего поста со ссылкой
    context_study: bool = False  # в посте рядом есть учебные слова

    @property
    def link(self) -> str:
        return f"https://t.me/+{self.key}" if self.kind == "invite" else f"https://t.me/{self.key}"

    @property
    def is_study(self) -> bool:
        return _is_study(self.title) or self.context_study

    def needs_check(self) -> bool:
        if self.status in FINAL_BAD:
            return False
        if self.status in OK_STATUSES:
            return time.time() - self.checked_at > RECHECK_DAYS * 86400
        return True

    def priority(self) -> tuple:
        # сначала: рядом учебные слова, потом свежие посты, приглашения раньше имён
        return (not self.context_study, -self.posted_at, self.kind != "invite")


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


def _load_cache() -> dict[str, Found]:
    if not CACHE_FILE.exists():
        return {}
    raw = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {k: Found(**v) for k, v in raw.items()}


def _save_cache(items: dict[str, Found]) -> None:
    data = {k: asdict(v) for k, v in items.items()}
    CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


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


async def _check_invite(client, item: Found) -> bool:
    """False — если Telegram просит ждать слишком долго и пора остановиться."""
    try:
        info = await _call(client, functions.messages.CheckChatInviteRequest(hash=item.key))
    except (InviteHashExpiredError, InviteHashInvalidError):
        item.status = "expired"
        return True
    except RPCError as e:
        item.status = f"error: {e.__class__.__name__}"
        return True
    if info is None:
        return False
    chat = getattr(info, "chat", None)
    if isinstance(info, ChatInvite):
        item.title = info.title
        item.members = info.participants_count
        item.chat_type = "channel" if info.broadcast else "group"
        item.needs_approval = bool(info.request_needed)
        item.status = "ok"
    elif isinstance(info, (ChatInviteAlready, ChatInvitePeek)):
        item.title = getattr(chat, "title", "")
        item.members = getattr(chat, "participants_count", None)
        item.chat_type = "channel" if getattr(chat, "broadcast", False) else "group"
        item.status = "already member"
    item.checked_at = time.time()
    return True


async def _check_public(client, item: Found) -> bool:
    try:
        entity = await client.get_entity(item.key)
    except FloodWaitError as e:
        print(f"Telegram asks to wait {e.seconds}s — stopping.", file=sys.stderr)
        return False
    except (ValueError, RPCError):
        item.status = "not a chat"  # не существует, человек или бот
        return True
    if not isinstance(entity, Channel):
        item.status = "not a chat"
        return True
    item.title = entity.title
    item.members = entity.participants_count
    item.chat_type = "group" if entity.megagroup else "channel"
    item.status = "ok"
    item.checked_at = time.time()
    return True


async def main(queries: list[str]) -> None:
    cfg = load_config()
    if not cfg.userbot_enabled:
        sys.exit("Fill API_ID and API_HASH in .env first.")
    items = _load_cache()

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

        # 2. Ссылки из постов: запоминаем дату и учебный ли контекст
        for i, seed in enumerate(top_seeds, 1):
            print(f"[read {i}/{len(top_seeds)}] @{seed.username}", file=sys.stderr)
            try:
                async for message in client.iter_messages(seed, limit=POSTS_PER_SEED):
                    invites, names = extract_links(message.raw_text, _message_urls(message))
                    posted = message.date.timestamp() if message.date else 0
                    study = _is_study(message.raw_text)
                    for kind, keys in (("invite", invites), ("public", names - seed_names)):
                        for key in keys:
                            item = items.setdefault(
                                f"{kind}:{key}", Found(kind, key, source="@" + seed.username)
                            )
                            item.posted_at = max(item.posted_at, posted)
                            item.context_study = item.context_study or study
            except FloodWaitError as e:
                print(f"Telegram asks to wait {e.seconds}s — stopping reading.", file=sys.stderr)
                break
            except RPCError as e:
                print(f"  cannot read @{seed.username}: {e}", file=sys.stderr)
            await asyncio.sleep(1)

        # 3. Проверка — по приоритету, не больше MAX_CHECKS за запуск
        queue = sorted((it for it in items.values() if it.needs_check()), key=Found.priority)
        print(f"To check: {len(queue)}, this run: {min(len(queue), MAX_CHECKS)}", file=sys.stderr)
        for i, item in enumerate(queue[:MAX_CHECKS], 1):
            print(f"[check {i}/{min(len(queue), MAX_CHECKS)}] {item.link}", file=sys.stderr)
            checker = _check_invite if item.kind == "invite" else _check_public
            if not await checker(client, item):
                break
            if i % 20 == 0:
                _save_cache(items)  # чтобы не потерять прогресс при обрыве
            await asyncio.sleep(2 if item.kind == "invite" else 3)

    _save_cache(items)
    _save_csv(items.values())
    _report(items.values(), cfg.userbot_public_chats)


def _save_csv(items) -> None:
    rows = sorted(
        (r for r in items if r.status in OK_STATUSES),
        key=lambda r: (not r.is_study, r.chat_type != "group", -(r.members or 0)),
    )
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")  # ";" — чтобы Excel сразу разбил на колонки
        writer.writerow(["study", "kind", "title", "members", "type", "needs approval",
                         "status", "last post", "link", "found in"])
        for r in rows:
            posted = (
                datetime.fromtimestamp(r.posted_at, timezone.utc).strftime("%Y-%m-%d")
                if r.posted_at else ""
            )
            writer.writerow(["yes" if r.is_study else "", r.kind, r.title, r.members or "",
                             r.chat_type, "yes" if r.needs_approval else "", r.status,
                             posted, r.link, r.source])


def _report(items, existing: list[str]) -> None:
    items = list(items)
    ok = [r for r in items if r.status in OK_STATUSES]
    study_groups = sorted(
        (r for r in ok if r.kind == "invite" and r.is_study and r.chat_type == "group"),
        key=lambda r: -(r.members or 0),
    )
    for r in study_groups[:30]:
        approval = "  (request to join)" if r.needs_approval else ""
        print(f"{r.members or '?':>7}  {r.title[:50]:<50} {r.link}{approval}")

    expired = sum(r.status in FINAL_BAD for r in items)
    left = sum(r.needs_check() for r in items)
    print(
        f"\nWorking links: {len(ok)} (study groups by invite: {len(study_groups)}). "
        f"Invalid, skipped forever: {expired}. Left to check next run: {left}."
    )
    print(f"Working links saved to {OUTPUT_FILE.resolve()}")

    known = {n.lstrip("@").lower() for n in existing}
    public_groups = [
        "@" + r.key for r in ok
        if r.kind == "public" and r.chat_type == "group" and r.is_study
        and r.key.lower() not in known
    ]
    if public_groups:
        print("\nLine for .env (old + new):\n")
        print("USERBOT_PUBLIC_CHATS=" + ",".join(existing + public_groups))


if __name__ == "__main__":
    asyncio.run(main(_load_queries(sys.argv[1:])))

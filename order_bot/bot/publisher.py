import asyncio
import logging
import time
from collections import Counter

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from . import formatter
from .config import Config
from .db import Database
from .detector import detect, fingerprint
from .models import RawMessage

log = logging.getLogger(__name__)


class Publisher:
    """Единая точка входа: любой источник отдаёт сюда RawMessage,
    дальше — фильтр, дедупликация, публикация."""

    def __init__(self, bot: Bot, db: Database, cfg: Config, bot_username: str):
        self.bot = bot
        self.db = db
        self.cfg = cfg
        self.bot_username = bot_username
        self._lock = asyncio.Lock()  # бот и юзербот могут прислать дубль одновременно
        self.stats: Counter[str] = Counter()  # для отчёта раз в 10 минут

    async def handle(self, msg: RawMessage) -> None:
        if msg.chat_id in self.cfg.own_channels:
            return
        self.stats["messages"] += 1
        result = detect(msg.text)
        if not result.is_order:
            self.stats[result.reason] += 1
            log.debug("skip %s/%s: %s", msg.chat_title, msg.message_id, result.reason)
            return

        fp = fingerprint(msg.text)
        async with self._lock:
            if self.db.is_duplicate(fp, self.cfg.dedup_hours):
                self.stats["duplicate"] += 1
                log.info("duplicate from %s", msg.chat_title)
                return
            free_due = (
                int(time.time()) + self.cfg.free_delay_minutes * 60
                if self.cfg.free_channel_id
                else None
            )
            order_id = self.db.add_order(msg, fp, result.work_type, result.budget, free_due)

        order = self.db.get_order(order_id)
        sent = await self._send(
            self.cfg.paid_channel_id,
            formatter.paid_post(order),
            formatter.paid_keyboard(order, self.bot_username, 0, self.cfg.contact_limit),
        )
        if sent:
            self.db.set_paid_message(order_id, sent.message_id)
            self.stats["published"] += 1
            log.info("order #%s from %s published", order_id, msg.chat_title)

    async def report_loop(self, every_minutes: int = 10) -> None:
        """Раз в 10 минут пишет в окно, сколько сообщений прочитано и что с ними стало."""
        while True:
            await asyncio.sleep(every_minutes * 60)
            s, self.stats = self.stats, Counter()
            skipped = ", ".join(
                f"{reason}: {n}"
                for reason, n in s.most_common()
                if reason not in ("messages", "published", "duplicate")
            )
            log.info(
                "last %d min: messages %d, published %d, duplicates %d, skipped — %s",
                every_minutes,
                s["messages"],
                s["published"],
                s["duplicate"],
                skipped or "none",
            )

    async def refresh_buttons(self, order_id: int) -> None:
        order = self.db.get_order(order_id)
        if not order or not order.paid_message_id:
            return
        opened = self.db.contacts_count(order_id)
        try:
            await self.bot.edit_message_reply_markup(
                chat_id=self.cfg.paid_channel_id,
                message_id=order.paid_message_id,
                reply_markup=formatter.paid_keyboard(
                    order, self.bot_username, opened, self.cfg.contact_limit
                ),
            )
        except TelegramBadRequest as e:  # "message is not modified" и т.п.
            log.debug("refresh buttons #%s: %s", order_id, e)

    async def free_channel_loop(self) -> None:
        """Раз в 30 секунд выкладывает в бесплатный канал заказы,
        у которых истекла задержка. Время хранится в БД, поэтому
        перезапуск бота ничего не теряет."""
        while True:
            for order in self.db.due_free_posts():
                await self._send(
                    self.cfg.free_channel_id,
                    formatter.free_post(order),
                    formatter.free_keyboard(self.cfg.subscribe_url),
                )
                self.db.mark_free_posted(order.id)
                await asyncio.sleep(1)
            await asyncio.sleep(30)

    async def _send(self, chat_id, text, keyboard):
        for _ in range(3):
            try:
                return await self.bot.send_message(chat_id, text, reply_markup=keyboard)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramBadRequest as e:
                log.error("send to %s failed: %s", chat_id, e)
                return None
        return None

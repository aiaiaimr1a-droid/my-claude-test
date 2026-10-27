"""Розрахунок нагадувань і форматування повідомлень (HTML)."""

from datetime import datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from .db import Task

WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]


def deadline_dt(task: Task, tz: ZoneInfo) -> datetime | None:
    """Момент дедлайну. Дата без часу вважається кінцем дня."""
    if task.deadline is None:
        return None
    value = datetime.fromisoformat(task.deadline)
    if not task.has_time:
        value = datetime.combine(value.date(), time(23, 59))
    return value.replace(tzinfo=tz)


def reminder_moments(task: Task, tz: ZoneInfo, digest_time: time,
                     soon_hours: int) -> tuple[datetime, datetime] | None:
    """(нагадування «за день», нагадування «скоро здача»).

    Для дедлайну без часу обидва нагадування приходять о DIGEST_TIME:
    напередодні і в день здачі — щоб не будити вночі.
    """
    due = deadline_dt(task, tz)
    if due is None:
        return None
    if task.has_time:
        return due - timedelta(days=1), due - timedelta(hours=soon_hours)
    day = due.date()
    return (datetime.combine(day - timedelta(days=1), digest_time, tz),
            datetime.combine(day, digest_time, tz))


def relative(task: Task, now: datetime, tz: ZoneInfo) -> str:
    due = deadline_dt(task, tz)
    if due is None:
        return ""
    if due < now:
        days = (now.date() - due.date()).days
        return "⚠️ прострочено" + (f" на {days} дн." if days else "")
    days = (due.date() - now.date()).days
    if days == 0:
        if task.has_time:
            hours = int((due - now).total_seconds() // 3600)
            return f"🔥 сьогодні, через {hours} год." if hours else "🔥 менш ніж за годину"
        return "🔥 сьогодні"
    if days == 1:
        return "⏰ завтра"
    return f"через {days} дн."


def format_deadline(task: Task, now: datetime, tz: ZoneInfo) -> str:
    if task.deadline is None:
        return "не вказано"
    due = deadline_dt(task, tz)
    text = f"{due:%d.%m.%Y} ({WEEKDAYS_SHORT[due.weekday()]})"
    if task.has_time:
        text += f", {due:%H:%M}"
    return f"{text} — {relative(task, now, tz)}"


def format_customer(task: Task) -> str:
    if not task.customer:
        return "не вказано"
    name = escape(task.customer)
    if task.customer_link:
        return f'<a href="{escape(task.customer_link)}">{name}</a>'
    return name


def format_card(task: Task, now: datetime, tz: ZoneInfo) -> str:
    status = " ✅ здано" if task.status == "done" else ""
    return (
        f"📌 <b>Задача #{task.id}</b>{status}\n\n"
        f"<b>Суть задачі:</b> {escape(task.summary)}\n"
        f"<b>Дедлайн:</b> {format_deadline(task, now, tz)}\n"
        f"<b>Ціна:</b> {escape(task.price) if task.price else 'не вказано'}\n"
        f"<b>Профіль замовника:</b> {format_customer(task)}"
    )


def _shorten(text: str, limit: int = 70) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_list(tasks: list[Task], now: datetime, tz: ZoneInfo, title: str) -> str:
    if not tasks:
        return f"{title}\n\nАктивних задач немає 🎉"
    entries = []
    for task in tasks:
        if task.deadline:
            due = deadline_dt(task, tz)
            when = f"{due:%d.%m} ({WEEKDAYS_SHORT[due.weekday()]})"
            if task.has_time:
                when += f" {due:%H:%M}"
            when += f" · {relative(task, now, tz)}"
        else:
            when = "без дедлайну"
        price = f" · {escape(task.price)}" if task.price else ""
        entries.append(f"<b>{when}</b>{price}\n{escape(_shorten(task.summary))}  /t{task.id}")
    return title + "\n\n" + "\n\n".join(entries)

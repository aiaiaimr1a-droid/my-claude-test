from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .detector import BUDGET_RE, mask_contacts
from .models import Order


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def paid_post(order: Order) -> str:
    return (
        f"📌 <b>Заказ #{order.id}</b>\n"
        f"📑 Тип: {escape(order.work_type or '—')}\n"
        f"💰 Бюджет: {escape(order.budget or 'не указан')}\n"
        f"📍 Источник: {escape(order.chat_title)}\n\n"
        f"{escape(_cut(mask_contacts(order.text), 900))}"
    )


def paid_keyboard(order: Order, bot_username: str, opened: int, limit: int) -> InlineKeyboardMarkup:
    if opened >= limit:
        button = InlineKeyboardButton(
            text="✅ Набран", url=f"https://t.me/{bot_username}?start=o_{order.id}"
        )
    else:
        button = InlineKeyboardButton(
            text=f"📩 Открыть контакт ({limit - opened} из {limit})",
            url=f"https://t.me/{bot_username}?start=o_{order.id}",
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def free_post(order: Order) -> str:
    return (
        f"📌 <b>Заказ #{order.id}</b> · {escape(order.work_type or '—')}\n\n"
        f"{escape(_cut(BUDGET_RE.sub('💰🔒', mask_contacts(order.text)), 300))}\n\n"
        "<i>Контакт и бюджет — в платном канале, заказы там появляются сразу.</i>"
    )


def free_keyboard(subscribe_url: str) -> InlineKeyboardMarkup | None:
    if not subscribe_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚡ Получать заказы сразу", url=subscribe_url)]]
    )


def contact_message(order: Order, left: int) -> str:
    lines = [f"📩 <b>Контакт по заказу #{order.id}</b>", ""]
    if order.author_username:
        lines.append(f"Заказчик: @{escape(order.author_username)}")
    elif order.author_id:
        lines.append(
            f'Заказчик: <a href="tg://user?id={order.author_id}">{escape(order.author_name)}</a>'
        )
    else:
        lines.append(f"Заказчик: {escape(order.author_name)}")
    if order.link:
        lines.append(f'Сообщение: <a href="{escape(order.link)}">открыть оригинал</a>')
    lines += ["", f"Осталось открытий у других авторов: {left}", "", escape(order.text)]
    return "\n".join(lines)

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .detector import hide_budget, mask_contacts
from .models import Order


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def paid_post(order: Order) -> str:
    return (
        f"📌 <b>Order #{order.id}</b>\n"
        f"📑 Type: {escape(order.work_type or '—')}\n"
        f"💰 Budget: {escape(order.budget or 'not specified')}\n"
        f"📍 Source: {escape(order.chat_title)}\n\n"
        f"{escape(_cut(mask_contacts(order.text), 900))}"
    )


def paid_keyboard(order: Order, bot_username: str, opened: int, limit: int) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start=o_{order.id}"
    if opened >= limit:
        text = "✅ Taken"
    else:
        text = f"📩 Get contact ({limit - opened} of {limit} left)"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])


def free_post(order: Order) -> str:
    return (
        f"📌 <b>Order #{order.id}</b> · {escape(order.work_type or '—')}\n\n"
        f"{escape(_cut(hide_budget(mask_contacts(order.text)), 300))}\n\n"
        "<i>Contact and budget are in the paid channel, where orders appear instantly.</i>"
    )


def free_keyboard(subscribe_url: str) -> InlineKeyboardMarkup | None:
    if not subscribe_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⚡ Get orders instantly", url=subscribe_url)]]
    )


def contact_message(order: Order, left: int, is_admin: bool = False) -> str:
    lines = [f"📩 <b>Contact for order #{order.id}</b>", ""]
    if order.author_username:
        lines.append(f"Client: @{escape(order.author_username)}")
    elif order.author_id:
        lines.append(
            f'Client: <a href="tg://user?id={order.author_id}">{escape(order.author_name)}</a>'
        )
    else:
        lines.append(f"Client: {escape(order.author_name)}")
    if order.link:
        lines.append(f'Message: <a href="{escape(order.link)}">open original</a>')
    lines.append("")
    if is_admin:
        lines.append(f"👑 Admin view: not counted. Contacts left for authors: {left}")
    else:
        lines.append(f"Contacts left for other authors: {left}")
    lines += ["", escape(order.text)]
    return "\n".join(lines)

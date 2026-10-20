from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Chat, Message

from . import formatter
from .config import Config
from .db import Database
from .models import RawMessage
from .publisher import Publisher

router = Router()

PAID_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.CREATOR,
}


def message_link(chat: Chat, message_id: int) -> str | None:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    if str(chat.id).startswith("-100"):  # приватная супергруппа / канал
        return f"https://t.me/c/{str(chat.id)[4:]}/{message_id}"
    return None  # обычная группа — прямых ссылок у неё нет


# --- Источник №1: группы и каналы, куда добавлен сам бот ---

@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
@router.channel_post()
async def on_source_message(message: Message, publisher: Publisher) -> None:
    text = message.text or message.caption
    if not text:
        return
    user = message.from_user
    if user and user.is_bot:
        return
    await publisher.handle(
        RawMessage(
            chat_id=message.chat.id,
            chat_title=message.chat.title or "—",
            message_id=message.message_id,
            link=message_link(message.chat, message.message_id),
            author_id=user.id if user else None,
            author_username=user.username if user else None,
            author_name=user.full_name if user else (message.chat.title or "—"),
            text=text,
        )
    )


# --- Личка с ботом: выдача контактов и админка ---

@router.message(CommandStart(deep_link=True), F.chat.type == ChatType.PRIVATE)
async def open_contact(
    message: Message, command: CommandObject, db: Database, cfg: Config, publisher: Publisher
) -> None:
    arg = command.args or ""
    if not arg.startswith("o_") or not arg[2:].isdigit():
        await message.answer("Invalid link.")
        return
    order = db.get_order(int(arg[2:]))
    if not order:
        await message.answer("Order not found.")
        return

    user_id = message.from_user.id
    is_admin = user_id in cfg.admin_ids
    if not is_admin:
        member = await message.bot.get_chat_member(cfg.paid_channel_id, user_id)
        if member.status not in PAID_STATUSES:
            text = "Contacts are available to paid channel members only."
            if cfg.subscribe_url:
                text += f"\nSubscribe here: {cfg.subscribe_url}"
            await message.answer(text)
            return

    # Проверка лимита и запись идут без await между ними — гонки нет.
    already = db.has_contact(order.id, user_id)
    opened = db.contacts_count(order.id)
    if not already and not is_admin:
        if opened >= cfg.contact_limit:
            await message.answer(
                f"Order #{order.id} is already taken: {cfg.contact_limit} authors have opened the contact."
            )
            return
        db.add_contact(order.id, user_id)
        opened += 1
        await publisher.refresh_buttons(order.id)

    await message.answer(
        formatter.contact_message(order, max(cfg.contact_limit - opened, 0), is_admin)
    )


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start(message: Message) -> None:
    await message.answer(
        "Hi! I collect student work orders into one channel.\n"
        "Tap «Get contact» under an order in the channel and I'll send you the client's contact."
    )


@router.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
async def stats(message: Message, db: Database, cfg: Config) -> None:
    if message.from_user.id not in cfg.admin_ids:
        return
    s = db.stats(days=7)
    sources = "\n".join(f"  • {title}: {n}" for title, n in s["by_source"]) or "  —"
    await message.answer(
        "📊 <b>Last 7 days</b>\n"
        f"Orders: {s['orders']}\n"
        f"Contacts opened: {s['opened']}\n"
        f"Active authors: {s['authors']}\n\n"
        f"Top sources:\n{sources}"
    )


@router.message(Command("id"))
async def chat_id(message: Message) -> None:
    """Помогает узнать ID чата при настройке. В каналах команды не работают —
    для канала перешлите любой его пост боту @userinfobot."""
    await message.answer(f"Chat ID: <code>{message.chat.id}</code>")

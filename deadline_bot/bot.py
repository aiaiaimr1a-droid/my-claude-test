"""Telegram-бот «табло дедлайнів».

Пересилаєш боту повідомлення замовника (і, за бажанням, пишеш дату) —
бот створює картку задачі, щоранку надсилає список дедлайнів і
нагадує за день до здачі та за кілька годин до неї.
"""

import logging
from datetime import datetime

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      Message, MessageOriginChannel, MessageOriginChat,
                      MessageOriginHiddenUser, MessageOriginUser, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler,
                          CommandHandler, ContextTypes, Defaults, MessageHandler,
                          filters)

from .config import Config, load_config
from .db import Database, Task
from .extractor import Extractor
from .formatting import format_card, format_list, reminder_moments, relative

log = logging.getLogger(__name__)

# Пересилання кількох повідомлень (і коментар до них) приходять окремими
# апдейтами — збираємо все, що прийшло за цей проміжок, в одну задачу.
BUFFER_SECONDS = 2.5
MAX_MESSAGE_LEN = 4000

HELP_TEXT = (
    "👋 Я твоє табло дедлайнів.\n\n"
    "• Перешли мені повідомлення замовника (можна кілька підряд) — я створю картку: "
    "суть задачі, дедлайн, ціна, профіль замовника.\n"
    "• Якщо дедлайну в повідомленні немає — просто напиши дату окремим повідомленням "
    "(«пʼятниця 18:00», «до 5.11»).\n"
    "• Щоранку надсилаю список дедлайнів, а також нагадую за день і за кілька годин до здачі.\n\n"
    "Команди:\n"
    "/list — усі активні дедлайни\n"
    "/t12 — відкрити задачу №12\n"
    "/help — ця довідка\n\n"
    "💡 Закріпи цей чат угорі списку — і всі дедлайни завжди під рукою."
)


# ---------- допоміжне ----------

def cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def now(context: ContextTypes.DEFAULT_TYPE) -> datetime:
    return datetime.now(cfg(context).tz)


def task_keyboard(task: Task) -> InlineKeyboardMarkup:
    if task.status == "done":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Повернути в роботу", callback_data=f"undo:{task.id}"),
            InlineKeyboardButton("🗑 Видалити", callback_data=f"del:{task.id}"),
        ]])
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Здано", callback_data=f"done:{task.id}"),
        InlineKeyboardButton("✏️ Дедлайн", callback_data=f"edit:{task.id}"),
        InlineKeyboardButton("🗑 Видалити", callback_data=f"del:{task.id}"),
    ]])


def split_message(text: str) -> list[str]:
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if current and len(current) + len(block) + 2 > MAX_MESSAGE_LEN:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


async def send_long(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    for chunk in split_message(text):
        await context.bot.send_message(chat_id, chunk, disable_web_page_preview=True)


def mark_past_reminders(task: Task, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Не надсилати нагадування, час яких уже минув на момент створення/зміни дедлайну."""
    c = cfg(context)
    moments = reminder_moments(task, c.tz, c.digest_time, c.soon_hours)
    if moments is None:
        return
    day_at, soon_at = moments
    current = now(context)
    db(context).set_reminded(task.id, day=day_at <= current, soon=soon_at <= current)


def describe_origin(message: Message) -> tuple[str | None, str | None]:
    """Профіль замовника з даних пересилання: (імʼя, посилання)."""
    origin = message.forward_origin
    if isinstance(origin, MessageOriginUser):
        user = origin.sender_user
        name = user.full_name
        if user.username:
            return f"{name} (@{user.username})", f"https://t.me/{user.username}"
        return name, f"tg://user?id={user.id}"
    if isinstance(origin, MessageOriginHiddenUser):
        return origin.sender_user_name, None
    if isinstance(origin, MessageOriginChat):
        chat = origin.sender_chat
        name = chat.title or chat.full_name or "чат"
        if origin.author_signature:
            name = f"{origin.author_signature} ({name})"
        return name, f"https://t.me/{chat.username}" if chat.username else None
    if isinstance(origin, MessageOriginChannel):
        chat = origin.chat
        name = chat.title or "канал"
        if origin.author_signature:
            name = f"{origin.author_signature} ({name})"
        link = f"https://t.me/{chat.username}/{origin.message_id}" if chat.username else None
        return name, link
    return None, None


# ---------- обробка повідомлень ----------

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = message.text or message.caption or ""
    is_forward = message.forward_origin is not None

    awaiting = context.chat_data.get("awaiting_deadline")
    if awaiting and not is_forward:
        context.chat_data.pop("awaiting_deadline", None)
        await apply_deadline(update, context, awaiting, text)
        return
    context.chat_data.pop("awaiting_deadline", None)

    if not text and not is_forward:
        return

    buffer = context.chat_data.setdefault("buffer", [])
    customer, link = describe_origin(message) if is_forward else (None, None)
    sent_at = (message.forward_origin.date if is_forward else message.date).astimezone(cfg(context).tz)
    buffer.append({
        "text": text or "[вкладення без тексту]",
        "forward": is_forward,
        "customer": customer,
        "link": link,
        "date": sent_at,
    })

    job = context.chat_data.get("buffer_job")
    if job is not None:
        job.schedule_removal()
    context.chat_data["buffer_job"] = context.job_queue.run_once(
        process_buffer, BUFFER_SECONDS, chat_id=message.chat_id,
        data=context.chat_data,
    )


async def process_buffer(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_data = context.job.data
    chat_data.pop("buffer_job", None)
    items = chat_data.pop("buffer", [])
    if not items:
        return
    chat_id = context.job.chat_id

    forwards = [i for i in items if i["forward"]]
    comment = "\n".join(i["text"] for i in items if not i["forward"])
    if forwards:
        client_messages = [(i["date"], i["text"]) for i in forwards]
    else:
        # Немає пересланих — сприймаємо власний текст як опис задачі.
        client_messages, comment = [(i["date"], i["text"]) for i in items], ""

    await context.bot.send_chat_action(chat_id, "typing")
    extracted = await context.application.bot_data["extractor"].extract_task(
        client_messages, comment, now(context))

    source_text = "\n\n".join(text for _, text in client_messages)
    if comment:
        source_text += f"\n\n[коментар] {comment}"

    origin = next((i for i in forwards if i["customer"]), None)
    if extracted is None:
        summary, deadline, price, customer = source_text[:500], None, None, None
        await context.bot.send_message(
            chat_id, "⚠️ Не вдалося автоматично розібрати повідомлення — зберіг як є.")
    else:
        summary, deadline = extracted.summary or source_text[:500], extracted.deadline
        price, customer = extracted.price, extracted.customer

    task = db(context).add(
        summary=summary,
        deadline=deadline,
        price=price,
        customer=origin["customer"] if origin else customer,
        customer_link=origin["link"] if origin else None,
        source_text=source_text,
    )
    mark_past_reminders(task, context)

    await context.bot.send_message(
        chat_id, format_card(task, now(context), cfg(context).tz),
        reply_markup=task_keyboard(task), disable_web_page_preview=True)
    if task.deadline is None:
        chat_data["awaiting_deadline"] = task.id
        await context.bot.send_message(
            chat_id, "📅 Дедлайн не знайшов. Напиши дату наступним повідомленням "
                     "(напр. «пʼятниця 18:00» або «5.11»).")


async def apply_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         task_id: int, text: str) -> None:
    task = db(context).get(task_id)
    if task is None:
        await update.effective_message.reply_text("Задачу не знайдено.")
        return
    deadline = await context.application.bot_data["extractor"].parse_deadline(text, now(context))
    if deadline is None:
        context.chat_data["awaiting_deadline"] = task_id
        await update.effective_message.reply_text(
            "Не зрозумів дату 🤔 Спробуй ще раз, напр. «завтра 15:00» або «12.11».")
        return
    db(context).set_deadline(task_id, deadline)
    task = db(context).get(task_id)
    mark_past_reminders(task, context)
    await update.effective_message.reply_text(
        format_card(task, now(context), cfg(context).tz),
        reply_markup=task_keyboard(task), disable_web_page_preview=True)


# ---------- команди ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = format_list(db(context).active(), now(context), cfg(context).tz, "📋 <b>Активні дедлайни</b>")
    await send_long(context, update.effective_chat.id, text)


async def cmd_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    task_id = int(context.matches[0].group(1))
    task = db(context).get(task_id)
    if task is None:
        await update.effective_message.reply_text("Задачу не знайдено.")
        return
    await update.effective_message.reply_text(
        format_card(task, now(context), cfg(context).tz),
        reply_markup=task_keyboard(task), disable_web_page_preview=True)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != cfg(context).owner_id:
        await query.answer()
        return
    action, raw_id = query.data.split(":")
    task_id = int(raw_id)
    task = db(context).get(task_id)
    if task is None:
        await query.answer("Задачу вже видалено")
        return

    if action == "del":
        db(context).delete(task_id)
        await query.answer("Видалено")
        await query.edit_message_text(f"🗑 Задачу #{task_id} видалено.")
        return
    if action == "edit":
        context.chat_data["awaiting_deadline"] = task_id
        await query.answer()
        await query.message.reply_text(f"📅 Напиши новий дедлайн для задачі #{task_id}.")
        return
    if action in ("done", "undo"):
        db(context).set_status(task_id, "done" if action == "done" else "active")
        await query.answer("Здано ✅" if action == "done" else "Повернуто в роботу")
        task = db(context).get(task_id)
        await query.edit_message_text(
            format_card(task, now(context), cfg(context).tz),
            reply_markup=task_keyboard(task), disable_web_page_preview=True)


async def not_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if cfg(context).owner_id is None and update.effective_message:
        await update.effective_message.reply_text(
            f"Твій Telegram ID: <code>{user.id}</code>\n"
            "Впиши його в OWNER_ID (файл .env) і перезапусти бота.")


# ---------- регулярні задачі ----------

async def morning_digest(context: ContextTypes.DEFAULT_TYPE) -> None:
    text = format_list(db(context).active(), now(context), cfg(context).tz,
                       "☀️ <b>Доброго ранку! Твої дедлайни:</b>")
    await send_long(context, context.job.chat_id, text)


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    c, database, current = cfg(context), db(context), now(context)
    for task in database.active():
        moments = reminder_moments(task, c.tz, c.digest_time, c.soon_hours)
        if moments is None:
            continue
        day_at, soon_at = moments
        if not task.reminded_soon and soon_at <= current:
            header = f"🔥 <b>Скоро здача!</b> ({relative(task, current, c.tz)})"
            database.set_reminded(task.id, day=True, soon=True)
        elif not task.reminded_day and day_at <= current:
            header = "⏰ <b>Нагадування: дедлайн завтра</b>"
            database.set_reminded(task.id, day=True)
        else:
            continue
        await context.bot.send_message(
            context.job.chat_id, f"{header}\n\n{format_card(task, current, c.tz)}",
            reply_markup=task_keyboard(task), disable_web_page_preview=True)


# ---------- запуск ----------

async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("list", "Усі активні дедлайни"),
        BotCommand("help", "Як користуватися"),
    ])


def build_app(config: Config) -> Application:
    app = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .defaults(Defaults(parse_mode=ParseMode.HTML, tzinfo=config.tz))
        .post_init(post_init)
        .build()
    )
    app.bot_data.update(config=config, db=Database(config.db_path), extractor=Extractor(config.model))

    if config.owner_id is None:
        log.warning("OWNER_ID не задано: напиши боту /start, щоб дізнатися свій ID")
        app.add_handler(MessageHandler(filters.ALL, not_owner))
        return app

    owner = filters.User(user_id=config.owner_id)
    app.add_handler(CommandHandler(["start", "help"], cmd_start, filters=owner))
    app.add_handler(CommandHandler(["list", "deadlines"], cmd_list, filters=owner))
    app.add_handler(MessageHandler(owner & filters.Regex(r"^/t(\d+)(@\w+)?$"), cmd_task))
    app.add_handler(CallbackQueryHandler(on_button, pattern=r"^(done|undo|edit|del):\d+$"))
    app.add_handler(MessageHandler(owner & ~filters.COMMAND, on_message))
    app.add_handler(MessageHandler(~owner, not_owner))

    app.job_queue.run_daily(morning_digest, time=config.digest_time.replace(tzinfo=config.tz),
                            chat_id=config.owner_id, name="digest")
    app.job_queue.run_repeating(check_reminders, interval=600, first=10,
                                chat_id=config.owner_id, name="reminders")
    return app


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    build_app(load_config()).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

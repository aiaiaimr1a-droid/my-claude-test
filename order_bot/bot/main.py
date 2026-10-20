import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from .config import load_config
from .db import Database
from .handlers import router
from .publisher import Publisher


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = load_config()
    db = Database(cfg.db_path)
    bot = Bot(
        cfg.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    me = await bot.get_me()
    publisher = Publisher(bot, db, cfg, me.username)

    # Всё, что передано в Dispatcher, aiogram подставит в хендлеры по имени аргумента
    dp = Dispatcher(db=db, cfg=cfg, publisher=publisher)
    dp.include_router(router)

    tasks = [dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())]
    if cfg.free_channel_id:
        tasks.append(publisher.free_channel_loop())
    if cfg.userbot_enabled:
        from .userbot import run_userbot

        tasks.append(run_userbot(cfg, publisher))

    logging.info("bot @%s started", me.username)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

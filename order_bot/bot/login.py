"""Вход юзербота по QR-коду — если код подтверждения не приходит.

    python -m bot.login

Откроет картинку с QR-кодом. На телефоне: Telegram -> Настройки -> Устройства ->
Подключить устройство -> отсканировать. Сессия сохранится в <USERBOT_SESSION>.session,
дальше bot.main и bot.find_groups будут входить сами.
"""

import asyncio
import getpass
import os
import webbrowser

import qrcode
import qrcode.image.svg
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import load_config

QR_FILE = os.path.abspath("login_qr.svg")


def _show_qr(url: str) -> None:
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(QR_FILE)
    webbrowser.open(f"file:///{QR_FILE}")


async def main() -> None:
    cfg = load_config()
    client = TelegramClient(cfg.userbot_session, cfg.api_id, cfg.api_hash)
    await client.connect()
    try:
        if await client.is_user_authorized():
            print("Already logged in.")
            return
        qr = await client.qr_login()
        while True:
            _show_qr(qr.url)
            print("Scan the QR code: Telegram -> Settings -> Devices -> Link Desktop Device")
            try:
                await qr.wait()
                break
            except asyncio.TimeoutError:
                print("QR code expired, showing a new one...")
                await qr.recreate()
            except SessionPasswordNeededError:
                await client.sign_in(password=getpass.getpass("Cloud password: "))
                break
        me = await client.get_me()
        print(f"Logged in as {me.first_name} ({me.phone}). You can close the QR page.")
    finally:
        await client.disconnect()
        if os.path.exists(QR_FILE):
            os.remove(QR_FILE)


if __name__ == "__main__":
    asyncio.run(main())

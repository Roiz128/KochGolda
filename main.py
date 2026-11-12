from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from telethon import TelegramClient

from bot import Notifier, build_router
from owners import Owners
from payments import build_payments_router
from config import load_config
from db import Database
from watcher import GiftWatcher


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = load_config()
    db = Database(cfg.db_path)

    client = TelegramClient(cfg.session, cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Сессия не авторизована. Сначала запусти: python login.py")

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    owners = Owners(db, cfg.owner_username, cfg.admin_ids)
    if db.get_meta("promo_seeded") != "1":
        db.add_promo("FREE5", 300, 0)  # пробный промокод: 5 минут, один раз на человека, без общего лимита
        db.set_meta("promo_seeded", "1")
    if cfg.owner_username:
        # определяем числовой id владельца по юзернейму; если не вышло, он запомнится при первом сообщении
        try:
            ent = await client.get_entity(cfg.owner_username)
            if type(ent).__name__ == "User" and not getattr(ent, "bot", False):
                owners.add(ent.id)
        except Exception as e:
            logging.getLogger("main").warning("Не удалось определить id @%s: %s", cfg.owner_username, e)
    notifier = Notifier(bot, db, owners)
    watcher = GiftWatcher(
        client,
        db,
        on_new=notifier.enqueue,
        rps=cfg.rps,
        refresh_minutes=cfg.refresh_minutes,
        extra_collections=cfg.extra_collections,
    )

    dp = Dispatcher()
    dp.include_router(build_router(db, watcher, owners))
    dp.include_router(build_payments_router(db, cfg, owners))

    tasks = [
        asyncio.create_task(notifier.run()),
        asyncio.create_task(notifier.expiry_loop()),
        asyncio.create_task(watcher.run_forever()),
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""Entry point:  python main.py"""
from __future__ import annotations

import asyncio
import logging

from bot.client import KillBot
from bot.config import load_config
from bot.database import Database


async def run() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    db = Database(config.database_path)
    await db.connect()
    bot = KillBot(config, db)
    try:
        async with bot:
            await bot.start(config.token)
    finally:
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass

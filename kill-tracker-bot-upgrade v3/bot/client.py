"""The bot object."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from .config import Config
from .database import Database
from .views import DecisionButton

log = logging.getLogger(__name__)

EXTENSIONS = ("bot.cogs.claims", "bot.cogs.admin", "bot.cogs.reports")


class KillBot(commands.Bot):
    def __init__(self, config: Config, db: Database) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # privileged: enable "Message Content Intent" in the developer portal
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.prefix),
            intents=intents,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True),
        )
        self.config = config
        self.db = db

    async def setup_hook(self) -> None:
        self.add_dynamic_items(DecisionButton)  # approval buttons survive restarts
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        log.info("Loaded %d extensions", len(EXTENSIONS))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d server(s)", self.user, getattr(self.user, "id", "?"), len(self.guilds))

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        error = getattr(error, "original", error)
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("This command only works inside a server.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("Only staff (Manage Server permission or a reviewer role) can use this command.")
        elif isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            await ctx.send(f"Missing or wrong input. Try `{self.config.prefix}help`.")
        else:
            log.exception("Unhandled error in command %s", ctx.command, exc_info=error)
            await ctx.send("Something went wrong. The error was logged.")

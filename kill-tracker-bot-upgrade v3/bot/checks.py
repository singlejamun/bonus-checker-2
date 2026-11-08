"""Who may configure the bot and approve claims."""
from __future__ import annotations

import discord
from discord.ext import commands


async def is_staff(bot: commands.Bot, member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    perms = member.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True
    role_ids = await bot.db.reviewer_role_ids(member.guild.id)  # type: ignore[attr-defined]
    return any(r.id in role_ids for r in member.roles)


def staff_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not await is_staff(ctx.bot, ctx.author):
            raise commands.CheckFailure("staff only")
        return True

    return commands.check(predicate)

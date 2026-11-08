"""??review, ??reviewrole, ??reportchannel, ??auditchannel, ??backup, ??audit, ??help."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from ..checks import staff_only
from ..embeds import COLORS, build_help_embed
from ..timeutil import week_window

log = logging.getLogger(__name__)


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _boundary(self) -> int:
        cfg = self.bot.config  # type: ignore[attr-defined]
        start, _ = week_window(datetime.now(timezone.utc), cfg.timezone, cfg.week_start_hour)
        return int(start.timestamp())

    @commands.command(name="help")
    async def help(self, ctx: commands.Context) -> None:
        await ctx.send(embed=build_help_embed(self.bot.config.prefix))  # type: ignore[attr-defined]

    @commands.command(name="review")
    @commands.guild_only()
    @staff_only()
    async def review(self, ctx: commands.Context, *, name: str = "") -> None:
        """??review <name> — creates the private approval channel."""
        name = name.strip()
        if not name:
            await ctx.send(f"Usage: `{ctx.prefix}review <name>`  e.g. `{ctx.prefix}review kill-approvals`")
            return
        guild = ctx.guild
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                read_message_history=True,
            ),
            ctx.author: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for role_id in await self.bot.db.reviewer_role_ids(guild.id):  # type: ignore[attr-defined]
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        old = (await self.bot.db.get_settings(guild.id) or {}).get("review_channel_id")  # type: ignore[attr-defined]
        try:
            channel = await guild.create_text_channel(
                name[:100],
                overwrites=overwrites,
                category=getattr(ctx.channel, "category", None),
                topic="Kill claim approvals. Accept / Reject / Edit each claim here.",
                reason=f"{ctx.prefix}review by {ctx.author}",
            )
        except discord.Forbidden:
            await ctx.send("I need the **Manage Channels** permission to create the review channel.")
            return
        except discord.HTTPException as exc:
            await ctx.send(f"Discord refused to create the channel: {exc.text}")
            return

        await self.bot.db.set_review_channel(guild.id, channel.id, self._boundary())  # type: ignore[attr-defined]
        msg = f"✅ Created {channel.mention}. Every claim will be sent there for approval."
        if old and old != channel.id:
            msg += f"\nThe old review channel <#{old}> is no longer used."
        await ctx.send(msg)
        await channel.send("This channel now receives kill claims. Only people who can see it (and staff) can approve.")

    @commands.command(name="reviewrole")
    @commands.guild_only()
    @staff_only()
    async def reviewrole(self, ctx: commands.Context, action: str, role: discord.Role) -> None:
        """??reviewrole add|remove @role — lets a role approve claims."""
        action = action.lower()
        if action == "add":
            await self.bot.db.add_reviewer_role(ctx.guild.id, role.id)  # type: ignore[attr-defined]
            settings = await self.bot.db.get_settings(ctx.guild.id)  # type: ignore[attr-defined]
            note = ""
            if settings and settings["review_channel_id"]:
                ch = ctx.guild.get_channel(settings["review_channel_id"])
                if ch:
                    try:
                        await ch.set_permissions(role, view_channel=True, send_messages=True, read_message_history=True)
                    except discord.HTTPException:
                        note = f"\n(I could not give the role access to {ch.mention}; do it by hand.)"
            await ctx.send(f"{role.mention} can now approve claims.{note}", allowed_mentions=discord.AllowedMentions.none())
        elif action == "remove":
            await self.bot.db.remove_reviewer_role(ctx.guild.id, role.id)  # type: ignore[attr-defined]
            await ctx.send(f"{role.mention} can no longer approve claims.", allowed_mentions=discord.AllowedMentions.none())
        else:
            await ctx.send(f"Usage: `{ctx.prefix}reviewrole add @role` or `{ctx.prefix}reviewrole remove @role`")

    @commands.command(name="reportchannel")
    @commands.guild_only()
    @staff_only()
    async def reportchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """??reportchannel [#channel] — where the Sunday report is posted."""
        channel = channel or ctx.channel  # type: ignore[assignment]
        await self.bot.db.set_report_channel(ctx.guild.id, channel.id, self._boundary())  # type: ignore[attr-defined]
        await ctx.send(f"The weekly report will be posted in {channel.mention} every Sunday.")

    @commands.command(name="auditchannel")
    @commands.guild_only()
    @staff_only()
    async def auditchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """??auditchannel [#channel] — where Accept/Reject/Edit actions are logged. Omit to clear."""
        if channel is None:
            # If no channel given and command has no mention, clear it
            await self.bot.db.set_audit_channel(ctx.guild.id, None)  # type: ignore[attr-defined]
            await ctx.send("Audit channel cleared. Accept/Reject/Edit will no longer be posted live.")
            return
        await self.bot.db.set_audit_channel(ctx.guild.id, channel.id)  # type: ignore[attr-defined]
        await ctx.send(f"Audit log will be posted in {channel.mention}.")

    @commands.command(name="audit")
    @commands.guild_only()
    @staff_only()
    async def audit(self, ctx: commands.Context) -> None:
        """Show the last 20 audit log entries."""
        rows = await self.bot.db.recent_audit(ctx.guild.id, limit=20)  # type: ignore[attr-defined]
        if not rows:
            await ctx.send("No audit entries yet.")
            return
        lines = []
        for r in rows:
            lines.append(
                f"<t:{r['created_at']}:R> **{r['action']}** by <@{r['actor_id']}> — {r['detail'][:120]}"
            )
        embed = discord.Embed(
            title="📋 Audit log (last 20)",
            description="\n".join(lines)[:4000],
            color=COLORS["info"],
        )
        await ctx.send(embed=embed)

    @commands.command(name="backup")
    @commands.guild_only()
    @staff_only()
    async def backup(self, ctx: commands.Context) -> None:
        """DM the database file to the command user (or attach here if DM fails)."""
        path = Path(self.bot.config.database_path)  # type: ignore[attr-defined]
        if not path.is_file():
            await ctx.send("Database file not found.")
            return
        file = discord.File(path, filename=f"killbot_backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.db")
        try:
            await ctx.author.send(content="📦 Kill tracker database backup:", file=file)
            await ctx.send("✅ Backup sent to your DMs.")
        except discord.HTTPException:
            # Re-open file since the previous File object may be consumed
            file2 = discord.File(path, filename=f"killbot_backup_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.db")
            await ctx.send(
                "Could not DM you (DMs closed?). Here is the backup in this channel:",
                file=file2,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))

"""??target / ??untarget / ??targets and the listener that reads kill messages."""
from __future__ import annotations

import logging
import re
import time

import discord
from discord.ext import commands

from ..checks import staff_only
from ..formatting import money, short_money
from ..parsing import parse_claim, resolve_price, split_target_args
from ..review import collect_screenshots
from ..views import build_panel, decision_view
from ..embeds import COLORS, build_review_embed

log = logging.getLogger(__name__)

MAX_NAME = 50
# Max claims a single user can submit per hour (rate limit protection)
MAX_CLAIMS_PER_HOUR = 5


class Claims(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- commands
    @commands.command(name="target")
    @commands.guild_only()
    @staff_only()
    async def target(self, ctx: commands.Context, *, args: str = "") -> None:
        """??target <name> [price[,price,...]]"""
        name, prices, ok = split_target_args(args)
        if not name or not ok:
            await ctx.send(
                f"Usage: `{ctx.prefix}target <name>` or `{ctx.prefix}target <name> 50000` "
                f"or `{ctx.prefix}target <name> 50000,1000000,2000000`"
            )
            return
        if len(name) > MAX_NAME:
            await ctx.send(f"Name is too long (max {MAX_NAME} characters).")
            return

        default_price = prices[0] if prices else self.bot.config.default_price  # type: ignore[attr-defined]
        await self.bot.db.upsert_target(  # type: ignore[attr-defined]
            ctx.channel.id, ctx.guild.id, name, default_price, prices or []
        )

        embed = discord.Embed(title="🎯 Target channel set", color=COLORS["accepted"])
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Default price per kill", value=money(default_price), inline=True)
        if prices and len(prices) > 1:
            embed.add_field(name="Price list", value=", ".join(short_money(p) for p in prices), inline=False)
        embed.add_field(
            name="How it works",
            value=(
                "Tag a member + `5x` / `5k` and attach the screenshot.\n"
                "Add a price like `100k` in the same message to use it for every kill in that message; "
                f"otherwise **{money(default_price)}** per kill is used."
            ),
            inline=False,
        )
        settings = await self.bot.db.get_settings(ctx.guild.id)  # type: ignore[attr-defined]
        if not settings or not settings["review_channel_id"]:
            embed.set_footer(text=f"⚠ No review channel yet. Run {ctx.prefix}review <name> so claims have somewhere to go.")
        await ctx.send(embed=embed)

    @commands.command(name="untarget")
    @commands.guild_only()
    @staff_only()
    async def untarget(self, ctx: commands.Context) -> None:
        if await self.bot.db.delete_target(ctx.channel.id):  # type: ignore[attr-defined]
            await ctx.send("This channel is no longer watched. Old approved kills are kept.")
        else:
            await ctx.send("This channel is not a target channel.")

    @commands.command(name="targets")
    @commands.guild_only()
    @staff_only()
    async def targets(self, ctx: commands.Context) -> None:
        rows = await self.bot.db.list_targets(ctx.guild.id)  # type: ignore[attr-defined]
        if not rows:
            await ctx.send(f"No target channels yet. Use `{ctx.prefix}target <name>` inside a channel.")
            return
        lines = []
        for r in rows[:50]:
            extra = f" · list: {', '.join(short_money(p) for p in r['prices'])}" if len(r["prices"]) > 1 else ""
            lines.append(f"**{r['name']}** — <#{r['channel_id']}> · {money(r['default_price'])}/kill{extra}")
        await ctx.send(embed=discord.Embed(title="Target channels", description="\n".join(lines), color=COLORS["info"]))

    @commands.command(name="edit")
    @commands.guild_only()
    @staff_only()
    async def edit(self, ctx: commands.Context, *, claim: str = "") -> None:
        """??edit <claim #> — edit a reviewed claim (kills, members, price per kill) or delete it."""
        m = re.search(r"\d+", claim)
        if not m:
            await ctx.send(f"Usage: `{ctx.prefix}edit <claim #>`  e.g. `{ctx.prefix}edit 12` (the number is on the claim card)")
            return
        sid = int(m.group())
        sub = await self.bot.db.get_submission(sid)  # type: ignore[attr-defined]
        if not sub or sub["guild_id"] != ctx.guild.id:
            await ctx.send(f"Claim **#{sid}** was not found.")
            return
        if sub["status"] == "rejected":
            await ctx.send(f"Claim **#{sid}** was rejected, so it is already not counted. Nothing to edit.")
            return
        if sub["status"] == "deleted":
            await ctx.send(f"Claim **#{sid}** was already deleted and is not counted.")
            return
        embed, view = await build_panel(self.bot, ctx.guild, sid)  # type: ignore[arg-type]
        await ctx.send(embed=embed, view=view)

    # ------------------------------------------------------------- listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not message.content:
            return
        prefix = self.bot.config.prefix  # type: ignore[attr-defined]
        if message.content.startswith(prefix):
            return

        target = await self.bot.db.get_target(message.channel.id)  # type: ignore[attr-defined]
        if not target:
            return
        claim = parse_claim(message.content)
        if not claim:
            return

        # Rate limit: max claims per user per hour
        since = int(time.time()) - 3600
        count = await self.bot.db.claim_count_since(message.guild.id, message.author.id, since)  # type: ignore[attr-defined]
        if count >= MAX_CLAIMS_PER_HOUR:
            await self._react(message, "🚫")
            try:
                await message.reply(
                    f"⏳ Rate limit: max **{MAX_CLAIMS_PER_HOUR}** claims per hour. Try again later.",
                    mention_author=True,
                    delete_after=30,
                )
            except discord.HTTPException:
                pass
            return

        settings = await self.bot.db.get_settings(message.guild.id)  # type: ignore[attr-defined]
        review_channel_id = settings["review_channel_id"] if settings else None
        review_channel = None
        if review_channel_id:
            review_channel = message.guild.get_channel(review_channel_id)
        if review_channel is None:
            await self._react(message, "⚠️")
            log.warning("Claim in #%s ignored: no review channel set for guild %s", message.channel, message.guild.id)
            return

        price, source = resolve_price(claim.price, target["default_price"], self.bot.config.default_price)  # type: ignore[attr-defined]
        sid = await self.bot.db.create_submission(  # type: ignore[attr-defined]
            guild_id=message.guild.id,
            target_channel_id=message.channel.id,
            target_name=target["name"],
            source_channel_id=message.channel.id,
            source_message_id=message.id,
            submitter_id=message.author.id,
            jump_url=message.jump_url,
            price=price,
            price_source=source,
            entries=claim.entries,
            created_at=int(message.created_at.timestamp()),
        )
        if sid is None:
            return  # already recorded

        await self.bot.db.record_claim_attempt(message.guild.id, message.author.id)  # type: ignore[attr-defined]

        # Build role mention for reviewers
        role_ids = await self.bot.db.reviewer_role_ids(message.guild.id)  # type: ignore[attr-defined]
        role_mentions = " ".join(f"<@&{rid}>" for rid in role_ids)
        content = role_mentions.strip() or None

        try:
            files, names, overflow = await collect_screenshots(message, message.guild.filesize_limit)
            sub = await self.bot.db.get_submission(sid)  # type: ignore[attr-defined]
            sub["shots"] = names
            embed = build_review_embed(sub, await self.bot.db.get_entries(sid))  # type: ignore[attr-defined]
            if overflow:
                embed.add_field(
                    name="More screenshots (original links)",
                    value="\n".join(overflow[:5])[:1000],
                    inline=False,
                )
            sent = await review_channel.send(  # type: ignore[union-attr]
                content=content,
                embed=embed,
                view=decision_view(sid),
                files=files,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            log.exception("Could not post review card for message %s", message.id)
            await self.bot.db.delete_submission(sid)  # type: ignore[attr-defined]
            await self._react(message, "⚠️")
            return

        await self.bot.db.set_review_message(sid, sent.channel.id, sent.id, names)  # type: ignore[attr-defined]
        await self._react(message, "⏳")

    @staticmethod
    async def _react(message: discord.Message, emoji: str) -> None:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Claims(bot))

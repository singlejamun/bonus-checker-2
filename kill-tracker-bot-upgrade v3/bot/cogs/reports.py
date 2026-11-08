"""Sunday 8 AM weekly report, mid-week snapshot, leaderboard, pending, mykills, etc."""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

from ..checks import staff_only
from ..embeds import (
    COLORS,
    build_leaderboard_embed,
    build_me_embed,
    build_pending_embed,
    build_player_embeds,
    build_report_embeds,
    build_top_embed,
)
from ..formatting import money
from ..sheet import build_workbook
from ..timeutil import fmt_window, week_window

log = logging.getLogger(__name__)

_USER_RE = re.compile(r"^(?:<@!?(\d{15,25})>|(\d{15,25}))$")
_LAST_WORDS = {"last", "previous", "prev"}


class Reports(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._tried: dict[int, int] = {}  # guild_id -> boundary we already tried (avoid retry spam)
        self._midweek_tried: dict[int, int] = {}

    async def cog_load(self) -> None:
        self.weekly_loop.start()
        self.midweek_loop.start()

    async def cog_unload(self) -> None:
        self.weekly_loop.cancel()
        self.midweek_loop.cancel()

    # ------------------------------------------------------------------ helpers
    def _window(self, weeks_back: int = 0) -> tuple[datetime, datetime]:
        cfg = self.bot.config  # type: ignore[attr-defined]
        return week_window(datetime.now(timezone.utc), cfg.timezone, cfg.week_start_hour, weeks_back)

    @staticmethod
    def _embed_text_size(embed: discord.Embed) -> int:
        """Return Discord's combined text size for one embed."""
        size = len(embed.title or "") + len(embed.description or "")
        size += len(embed.author.name or "") if embed.author else 0
        size += len(embed.footer.text or "") if embed.footer else 0
        size += sum(len(field.name or "") + len(field.value or "") for field in embed.fields)
        return size

    @classmethod
    async def _send_embeds(cls, dest: discord.abc.Messageable, embeds: list[discord.Embed]) -> None:
        """Send embeds in batches that respect Discord's 10-embed and 6,000-char limits."""
        batch: list[discord.Embed] = []
        batch_size = 0
        for embed in embeds:
            size = cls._embed_text_size(embed)
            if size > 6000:
                raise ValueError(f"A single report embed is too large ({size} characters).")
            if batch and (len(batch) >= 10 or batch_size + size > 6000):
                await dest.send(embeds=batch)
                batch = []
                batch_size = 0
            batch.append(embed)
            batch_size += size
        if batch:
            await dest.send(embeds=batch)

    async def build_weekly(self, guild_id: int, weeks_back: int) -> list[discord.Embed]:
        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        rows = await self.bot.db.totals(guild_id, s, e)  # type: ignore[attr-defined]
        pending = await self.bot.db.pending_count(guild_id, s, e)  # type: ignore[attr-defined]
        return build_report_embeds("📊 Weekly kill report", fmt_window(start, end), rows, pending)

    async def _member_names(self, guild: discord.Guild, user_ids: list[int]) -> dict[int, str]:
        names: dict[int, str] = {}
        for uid in user_ids:
            member = guild.get_member(uid)
            if member is None:
                try:
                    member = await guild.fetch_member(uid)
                except discord.HTTPException:
                    member = None
            if member is not None:
                names[uid] = member.display_name
        return names

    async def get_warnings(self, guild: discord.Guild, user_ids: list[int]) -> dict[int, int]:
        """Number of warnings per member. Not connected yet: staff type them in the sheet."""
        return {}

    async def build_sheet(self, guild: discord.Guild, weeks_back: int) -> discord.File:
        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        db = self.bot.db  # type: ignore[attr-defined]
        rows = await db.totals(guild.id, s, e)
        targets = await db.list_targets(guild.id)
        rates = {t["name"].lower(): int(t["default_price"]) for t in targets}
        names = await self._member_names(guild, sorted({int(r["user_id"]) for r in rows}))
        warnings = await self.get_warnings(guild, sorted({int(r["user_id"]) for r in rows}))
        data = build_workbook(rows, [t["name"] for t in targets], rates, names, fmt_window(start, end), warnings)
        fname = f"bonus_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx"
        return discord.File(io.BytesIO(data), filename=fname)

    async def send_full_report(
        self,
        dest: discord.abc.Messageable,
        guild: discord.Guild,
        weeks_back: int,
        *,
        late: bool = False,
    ) -> None:
        if late:
            await dest.send(
                "⏰ **Missed Sunday recovery** — the bot was offline (or just started) at the scheduled time. "
                "Posting the missed weekly report now."
            )
        await self._send_embeds(dest, await self.build_weekly(guild.id, weeks_back))
        await dest.send(content="Excel sheet for this week:", file=await self.build_sheet(guild, weeks_back))

        # Auto-reset confirmation + summary
        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        summary = await self.bot.db.week_summary(guild.id, s, e)  # type: ignore[attr-defined]
        embed = discord.Embed(
            title="🔄 Week closed — counters reset for the new week",
            description=(
                f"**Window:** {fmt_window(start, end)}\n\n"
                f"• **Members with kills:** {summary['members']}\n"
                f"• **Total kills:** {summary['kills']}\n"
                f"• **Total money:** {money(summary['amount'])}\n"
                f"• **Pending claims left:** {summary['pending']}\n\n"
                "A new week has started. `??bonus` and current-week sheets now start from **0**."
            ),
            color=COLORS["accepted"],
        )
        await dest.send(embed=embed)

    # ------------------------------------------------------------ scheduled jobs
    @tasks.loop(minutes=1)
    async def weekly_loop(self) -> None:
        start, _ = self._window(0)
        boundary = int(start.timestamp())
        now = datetime.now(timezone.utc)
        # Detect if we're posting late (more than 5 min after boundary)
        late = (now.timestamp() - boundary) > 300

        for g in await self.bot.db.list_configured_guilds():  # type: ignore[attr-defined]
            guild_id = g["guild_id"]
            if g["last_report_boundary"] >= boundary or self._tried.get(guild_id) == boundary:
                continue
            guild = self.bot.get_guild(guild_id)
            channel_id = g["report_channel_id"] or g["review_channel_id"]
            if guild is None or not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            self._tried[guild_id] = boundary
            try:
                await self.send_full_report(channel, guild, weeks_back=1, late=late)
            except discord.HTTPException:
                log.exception("Weekly report failed for guild %s", guild_id)
                continue
            await self.bot.db.set_last_report_boundary(guild_id, boundary)  # type: ignore[attr-defined]
            log.info("Weekly report posted for guild %s (late=%s)", guild_id, late)

    @weekly_loop.before_loop
    async def _before_weekly(self) -> None:
        await self.bot.wait_until_ready()

    @weekly_loop.error
    async def _weekly_error(self, error: BaseException) -> None:
        log.exception("weekly_loop crashed", exc_info=error)

    @tasks.loop(minutes=5)
    async def midweek_loop(self) -> None:
        """Auto mid-week snapshot every Wednesday around 12:00 local time."""
        cfg = self.bot.config  # type: ignore[attr-defined]
        now_local = datetime.now(timezone.utc).astimezone(cfg.timezone)
        if now_local.weekday() != 2:  # Wednesday
            return
        if not (11 <= now_local.hour <= 13):
            return
        # Use the date as boundary so we only post once per Wednesday
        boundary = int(datetime.combine(now_local.date(), datetime.min.time(), tzinfo=cfg.timezone).timestamp())

        for g in await self.bot.db.list_configured_guilds():  # type: ignore[attr-defined]
            guild_id = g["guild_id"]
            if (g.get("last_midweek_boundary") or 0) >= boundary or self._midweek_tried.get(guild_id) == boundary:
                continue
            guild = self.bot.get_guild(guild_id)
            channel_id = g["report_channel_id"] or g["review_channel_id"]
            if guild is None or not channel_id:
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            self._midweek_tried[guild_id] = boundary
            try:
                start, end = self._window(0)
                s, e = int(start.timestamp()), int(end.timestamp())
                sheet_file = await self.build_sheet(guild, weeks_back=0)
                sheet_file.filename = f"MIDWEEK_bonus_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx"
                await channel.send(file=sheet_file)
            except discord.HTTPException:
                log.exception("Mid-week snapshot failed for guild %s", guild_id)
                continue
            await self.bot.db.set_last_midweek_boundary(guild_id, boundary)  # type: ignore[attr-defined]
            log.info("Mid-week snapshot posted for guild %s", guild_id)

    @midweek_loop.before_loop
    async def _before_midweek(self) -> None:
        await self.bot.wait_until_ready()

    @midweek_loop.error
    async def _midweek_error(self, error: BaseException) -> None:
        log.exception("midweek_loop crashed", exc_info=error)

    # ----------------------------------------------------------------- commands
    @commands.command(name="weekly")
    @commands.guild_only()
    @staff_only()
    async def weekly(self, ctx: commands.Context) -> None:
        """Post last week's full report + Excel + reset summary here, right now."""
        await self.send_full_report(ctx.channel, ctx.guild, weeks_back=1)

    @commands.command(name="bonus")
    @commands.guild_only()
    @staff_only()
    async def bonus(self, ctx: commands.Context, *, args: str = "") -> None:
        """??bonus <target name | @member> [last] — kills and money for one Sunday 8 AM → Sunday 8 AM week."""
        tokens = args.split()
        weeks_back = 0
        if tokens and tokens[-1].lower() in _LAST_WORDS:
            weeks_back = 1
            tokens = tokens[:-1]
        subject = " ".join(tokens).strip()

        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        window_text = fmt_window(start, end) + ("" if weeks_back else "\n*(current week, still running)*")
        title = "🎁 Bonus — last week" if weeks_back else "🎁 Bonus — this week"
        db = self.bot.db  # type: ignore[attr-defined]
        pending = await db.pending_count(ctx.guild.id, s, e)

        if not subject:
            rows = await db.totals(ctx.guild.id, s, e)
            embeds = build_report_embeds(title, window_text, rows, pending)
        else:
            m = _USER_RE.match(subject)
            if m:
                uid = int(m.group(1) or m.group(2))
                rows = await db.totals(ctx.guild.id, s, e, user_id=uid)
                embeds = build_player_embeds(title, window_text, uid, rows, pending)
            else:
                rows = await db.totals(ctx.guild.id, s, e, target_name=subject)
                if not rows and not await db.target_name_exists(ctx.guild.id, subject):
                    names = ", ".join(f"`{t['name']}`" for t in await db.list_targets(ctx.guild.id)) or "none yet"
                    await ctx.send(f"No target called **{subject}**. Known targets: {names}")
                    return
                embeds = build_report_embeds(f"{title} · {subject}", window_text, rows, pending)
        await self._send_embeds(ctx.channel, embeds)

    @commands.command(name="sheet")
    @commands.guild_only()
    @staff_only()
    async def sheet(self, ctx: commands.Context, *, args: str = "") -> None:
        """Excel sheet for the current week (or `last` = previous full week)."""
        weeks_back = 1 if args.strip().lower() in _LAST_WORDS else 0
        await ctx.send(file=await self.build_sheet(ctx.guild, weeks_back))

    @commands.command(name="testbonus")
    @commands.guild_only()
    @staff_only()
    async def testbonus(self, ctx: commands.Context, *, args: str = "") -> None:
        """Send only the test Excel preview. Nothing is paid. Use `last` for the previous full week."""
        weeks_back = 1 if args.strip().lower() in _LAST_WORDS else 0
        start, end = self._window(weeks_back)
        # Excel-only preview: no embeds, so large reports cannot hit Discord's embed size limit.
        sheet_file = await self.build_sheet(ctx.guild, weeks_back)
        sheet_file.filename = f"TEST_bonus_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx"
        await ctx.send(file=sheet_file)

    async def _board(self, ctx: commands.Context, args: str, label: str, icon: str = "🏆") -> dict:
        """Shared data for ??lb and ??top."""
        weeks_back = 1 if args.strip().lower() in _LAST_WORDS else 0
        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        db = self.bot.db  # type: ignore[attr-defined]
        everyone = await db.leaderboard(ctx.guild.id, s, e, limit=1000)
        summary = await db.week_summary(ctx.guild.id, s, e)
        viewer_rank = viewer_row = None
        for i, row in enumerate(everyone, start=1):
            if int(row["user_id"]) == ctx.author.id:
                viewer_rank, viewer_row = i, row
                break
        return {
            "title": f"{icon} {label} · {'Last week' if weeks_back else 'This week'}",
            "window_text": fmt_window(start, end) + ("" if weeks_back else "\n*(current week, still running)*"),
            "everyone": everyone,
            "summary": summary,
            "viewer_id": ctx.author.id,
            "viewer_rank": viewer_rank,
            "viewer_row": viewer_row,
            "guild_name": ctx.guild.name,
            "guild_icon_url": ctx.guild.icon.url if ctx.guild.icon else None,
        }

    @commands.command(name="lb", aliases=["leaderboard"])
    @commands.guild_only()
    async def lb(self, ctx: commands.Context, *, args: str = "") -> None:
        """??lb [last] — podium (top 3 side by side) + ranks 4-15."""
        d = await self._board(ctx, args, "Leaderboard")
        everyone = d.pop("everyone")
        await ctx.send(embed=build_leaderboard_embed(d.pop("title"), d.pop("window_text"), everyone[:15], **d))

    @commands.command(name="top")
    @commands.guild_only()
    async def top(self, ctx: commands.Context, *, args: str = "") -> None:
        """??top [last] — top 15 killers, one line each."""
        d = await self._board(ctx, args, "Top 15")
        everyone = d.pop("everyone")
        await ctx.send(embed=build_top_embed(d.pop("title"), d.pop("window_text"), everyone[:15], **d))

    @commands.command(name="midweek")
    @commands.guild_only()
    @staff_only()
    async def midweek(self, ctx: commands.Context) -> None:
        """Send the current-week Excel snapshot only."""
        start, end = self._window(0)
        sheet_file = await self.build_sheet(ctx.guild, weeks_back=0)
        sheet_file.filename = f"MIDWEEK_bonus_{start:%Y-%m-%d}_to_{end:%Y-%m-%d}.xlsx"
        await ctx.send(file=sheet_file)

    @commands.command(name="pending")
    @commands.guild_only()
    @staff_only()
    async def pending(self, ctx: commands.Context) -> None:
        """List all pending claims waiting for review."""
        rows = await self.bot.db.list_pending(ctx.guild.id, limit=25)  # type: ignore[attr-defined]
        await ctx.send(embed=build_pending_embed(rows))

    @commands.command(name="me", aliases=["mykills", "mybonus"])
    @commands.guild_only()
    async def me(self, ctx: commands.Context, *, args: str = "") -> None:
        """??me [last] — your kills, money, rank and the players right above / below you."""
        weeks_back = 1 if args.strip().lower() in _LAST_WORDS else 0
        start, end = self._window(weeks_back)
        s, e = int(start.timestamp()), int(end.timestamp())
        uid = ctx.author.id
        db = self.bot.db  # type: ignore[attr-defined]
        rows = await db.totals(ctx.guild.id, s, e, user_id=uid)
        pending = await db.pending_count(ctx.guild.id, s, e)
        everyone = await db.leaderboard(ctx.guild.id, s, e, limit=1000)
        idx = next((i for i, row in enumerate(everyone) if int(row["user_id"]) == uid), None)
        rank = idx + 1 if idx is not None else None
        ahead = everyone[idx - 1] if idx is not None and idx > 0 else None
        behind = everyone[idx + 1] if idx is not None and idx + 1 < len(everyone) else None
        window_text = fmt_window(start, end) + ("" if weeks_back else "\n*(current week, still running)*")
        title = "⚔️ Your rank card · Last week" if weeks_back else "⚔️ Your rank card · This week"
        embed = build_me_embed(
            title,
            window_text,
            uid,
            rows,
            display_name=ctx.author.display_name,
            avatar_url=ctx.author.display_avatar.url,
            rank=rank,
            players=len(everyone),
            ahead=ahead,
            behind=behind,
            leader=everyone[0] if everyone else None,
            pending=pending,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reports(bot))

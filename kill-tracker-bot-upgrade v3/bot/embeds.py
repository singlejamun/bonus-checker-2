"""Builds every embed the bot sends."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from .formatting import chunk_lines, money

COLORS = {
    "pending": 0xF1C40F,
    "accepted": 0x2ECC71,
    "rejected": 0xE74C3C,
    "info": 0x5865F2,
    "deleted": 0x95A5A6,
    "gold": 0xF5B800,
    "player": 0x57F287,
}
PRICE_SOURCE_TEXT = {
    "message": "price written in the message",
    "target": "channel price",
    "default": "default price",
    "edited": "edited by staff",
}
MAX_ENTRY_LINES = 40


def build_review_embed(sub: dict[str, Any], entries: list[dict[str, Any]]) -> discord.Embed:
    """The approval card shown in the review channel."""
    price = int(sub["price"])
    status = sub["status"]
    total_kills = sum(int(e["kills"]) for e in entries)
    total_amount = total_kills * price

    lines = [
        f"<@{e['user_id']}> — **{e['kills']}** × {money(price)} = **{money(int(e['kills']) * price)}**"
        for e in entries[:MAX_ENTRY_LINES]
    ]
    if len(entries) > MAX_ENTRY_LINES:
        lines.append(f"…and {len(entries) - MAX_ENTRY_LINES} more")
    if not entries:
        lines = ["*No members left — add one with Edit, or reject.*"]

    embed = discord.Embed(
        title=f"Kill claim #{sub['id']} · {sub['target_name']}",
        description="\n".join(lines),
        color=COLORS.get(status, COLORS["info"]),
    )
    embed.add_field(name="Total", value=f"**{total_kills}** kills · **{money(total_amount)}**", inline=True)
    embed.add_field(
        name="Price per kill",
        value=f"{money(price)}\n*{PRICE_SOURCE_TEXT.get(sub['price_source'], sub['price_source'])}*",
        inline=True,
    )
    source = f"<#{sub['source_channel_id']}> · by <@{sub['submitter_id']}>"
    if sub.get("jump_url"):
        source += f"\n[Jump to message]({sub['jump_url']})"
    source += f"\n<t:{sub['created_at']}:f>"
    embed.add_field(name="Source", value=source, inline=False)

    if not sub.get("shots"):
        embed.add_field(name="⚠ Screenshot", value="No screenshot was attached to this message.", inline=False)
    else:
        embed.set_image(url=f"attachment://{sub['shots'][0]}")

    if status == "pending":
        embed.set_footer(text="Pending review")
    elif status == "accepted":
        embed.add_field(
            name="✅ Accepted", value=f"by <@{sub['decided_by']}> · <t:{sub['decided_at']}:R>", inline=False
        )
    elif status == "rejected":
        reason = (sub.get("reject_reason") or "—")[:900]
        embed.add_field(
            name="❌ Rejected",
            value=f"by <@{sub['decided_by']}> · <t:{sub['decided_at']}:R>\n**Reason:** {reason}",
            inline=False,
        )
    elif status == "deleted":
        who = f"by <@{sub['deleted_by']}> · " if sub.get("deleted_by") else ""
        when = f"<t:{sub['deleted_at']}:R>" if sub.get("deleted_at") else ""
        embed.add_field(
            name="🗑️ Deleted",
            value=f"{who}{when}\nThis claim is **not counted** in any bonus or report.".strip(),
            inline=False,
        )
    return embed


def build_edit_panel_embed(sub: dict[str, Any], entries: list[dict[str, Any]]) -> discord.Embed:
    price = int(sub["price"])
    status = sub.get("status", "pending")
    total_kills = sum(int(e["kills"]) for e in entries)
    lines = [
        f"<@{e['user_id']}> — **{e['kills']}** kills · {money(int(e['kills']) * price)}"
        for e in entries[:MAX_ENTRY_LINES]
    ] or ["*(no members)*"]
    embed = discord.Embed(
        title=f"Editing claim #{sub['id']} · {sub['target_name']}",
        description="\n".join(lines),
        color=COLORS.get(status, COLORS["info"]),
    )
    embed.add_field(name="Status", value=status.capitalize(), inline=True)
    embed.add_field(name="Price per kill", value=money(price), inline=True)
    embed.add_field(name="Total", value=f"**{total_kills}** kills · **{money(total_kills * price)}**", inline=True)
    if status == "accepted":
        footer = "Already accepted: every change updates the bonus right away. Delete claim = not counted."
    else:
        footer = "Pick a member to change kills (0 removes). Changes show on the claim immediately."
    embed.set_footer(text=footer)
    return embed


# ---------------------------------------------------------------- reports
def _ranking_lines(rows: list[dict[str, Any]]) -> list[str]:
    """rows: dicts with user_id, kills, amount (already one per user)."""
    ordered = sorted(rows, key=lambda r: (-int(r["kills"]), int(r["user_id"])))
    return [
        f"`{i:>2}.` <@{r['user_id']}> — **{int(r['kills'])}** kills · {money(int(r['amount']))}"
        for i, r in enumerate(ordered, start=1)
    ]


def _sum_by_user(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[int, dict[str, Any]] = {}
    for r in rows:
        a = acc.setdefault(int(r["user_id"]), {"user_id": int(r["user_id"]), "kills": 0, "amount": 0})
        a["kills"] += int(r["kills"])
        a["amount"] += int(r["amount"])
    return list(acc.values())


def _totals_text(rows: list[dict[str, Any]]) -> str:
    kills = sum(int(r["kills"]) for r in rows)
    amount = sum(int(r["amount"]) for r in rows)
    return f"**Total:** {kills} kills · {money(amount)}"


def build_report_embeds(
    title: str, window_text: str, rows: list[dict[str, Any]], pending: int = 0
) -> list[discord.Embed]:
    """One section per target name, plus an 'All targets' summary when there is more than one."""
    embeds: list[discord.Embed] = []
    note = f"⚠ {pending} claim(s) from this window are still pending review." if pending else None

    if not rows:
        e = discord.Embed(title=title, description=f"{window_text}\n\nNo approved kills in this window.", color=COLORS["info"])
        if note:
            e.set_footer(text=note)
        return [e]

    by_target: dict[str, list[dict[str, Any]]] = {}
    display: dict[str, str] = {}
    for r in rows:
        key = r["target_name"].lower()
        display.setdefault(key, r["target_name"])
        by_target.setdefault(key, []).append(r)

    sections: list[tuple[str, list[dict[str, Any]]]] = [(display[k], v) for k, v in sorted(by_target.items())]
    if len(sections) > 1:
        sections.append(("All targets", rows))

    first = True
    for name, trows in sections:
        per_user = _sum_by_user(trows)
        pages = chunk_lines(_ranking_lines(per_user) + ["", _totals_text(trows)])
        for n, page in enumerate(pages, start=1):
            heading = f"{title} · {name}" if len(sections) > 1 or name != title else title
            if len(pages) > 1:
                heading += f" ({n}/{len(pages)})"
            desc = f"{window_text}\n\n{page}" if first else page
            embeds.append(discord.Embed(title=heading[:250], description=desc, color=COLORS["info"]))
            first = False
    if note:
        embeds[-1].set_footer(text=note)
    return embeds


def build_player_embeds(
    title: str, window_text: str, user_id: int, rows: list[dict[str, Any]], pending: int = 0
) -> list[discord.Embed]:
    if not rows:
        e = discord.Embed(
            title=title, description=f"{window_text}\n\n<@{user_id}> has no approved kills in this window.",
            color=COLORS["info"],
        )
        if pending:
            e.set_footer(text=f"⚠ {pending} claim(s) from this window are still pending review.")
        return [e]
    lines = [
        f"**{r['target_name']}** — {int(r['kills'])} kills · {money(int(r['amount']))}"
        for r in sorted(rows, key=lambda r: (-int(r["kills"]), r["target_name"].lower()))
    ]
    lines += ["", _totals_text(rows)]
    pages = chunk_lines(lines)
    out = []
    for n, page in enumerate(pages, start=1):
        head = f"{window_text}\n<@{user_id}>\n\n{page}" if n == 1 else page
        out.append(discord.Embed(title=title, description=head, color=COLORS["info"]))
    if pending:
        out[-1].set_footer(text=f"⚠ {pending} claim(s) from this window are still pending review.")
    return out


def build_test_bonus_embeds(
    title: str, window_text: str, rows: list[dict[str, Any]], pending: int = 0
) -> list[discord.Embed]:
    """Every player with their total and a per-target breakdown (used by the test command)."""
    head = f"{window_text}\n🧪 **TEST RUN** — nothing is paid or changed by this command."
    if not rows:
        return [discord.Embed(title=title, description=f"{head}\n\nNo approved kills in this window.", color=COLORS["info"])]

    per_user: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        per_user.setdefault(int(r["user_id"]), []).append(r)

    def user_total(uid: int) -> tuple[int, int]:
        rs = per_user[uid]
        return sum(int(r["kills"]) for r in rs), sum(int(r["amount"]) for r in rs)

    order = sorted(per_user, key=lambda u: (-user_total(u)[0], u))
    lines: list[str] = []
    for i, uid in enumerate(order, start=1):
        kills, amount = user_total(uid)
        lines.append(f"`{i:>2}.` <@{uid}> — **{kills}** kills · **{money(amount)}**")
        if len(per_user[uid]) > 1:
            for r in sorted(per_user[uid], key=lambda r: r["target_name"].lower()):
                lines.append(f"      ↳ {r['target_name']}: {int(r['kills'])} kills · {money(int(r['amount']))}")
    lines += ["", _totals_text(rows) + f" · {len(order)} players"]

    pages = chunk_lines(lines)
    out = []
    for n, page in enumerate(pages, start=1):
        t = title + (f" ({n}/{len(pages)})" if len(pages) > 1 else "")
        out.append(discord.Embed(title=t, description=f"{head}\n\n{page}" if n == 1 else page, color=COLORS["info"]))
    if pending:
        out[-1].set_footer(text=f"⚠ {pending} claim(s) from this window are still pending review and not counted.")
    return out


def progress_bar(value: int, maximum: int, width: int = 10) -> str:
    """▰▰▰▰▱▱▱▱▱▱ — a bar relative to `maximum` (at least one block when value > 0)."""
    if maximum <= 0 or value <= 0:
        return "▱" * width
    filled = max(1, round(width * value / maximum))
    filled = min(width, filled)
    return "▰" * filled + "▱" * (width - filled)


def _now() -> datetime:
    return datetime.now(timezone.utc)


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _kills_word(n: int) -> str:
    return f"{n} kill" if n == 1 else f"{n} kills"


def _footer_for_viewer(base: str, viewer_id: int | None, viewer_rank: int | None) -> str:
    if viewer_rank is not None:
        return f"{base} · You are #{viewer_rank}"
    if viewer_id is not None:
        return f"{base} · You have no approved kills yet"
    return base


def _summary_text(rows: list[dict[str, Any]], summary: dict[str, Any] | None) -> str:
    if summary is not None:
        players, kills, amount = int(summary["members"]), int(summary["kills"]), int(summary["amount"])
    else:
        players = len(rows)
        kills = sum(int(r["kills"]) for r in rows)
        amount = sum(int(r["amount"]) for r in rows)
    return f"{kills:,} kills · {money(amount)} · {players} players"


def _split_field_lines(lines: list[str], limit: int = 1000) -> list[str]:
    """Group lines into chunks that each fit one embed field (1024 chars)."""
    chunks: list[str] = []
    cur = ""
    for line in lines:
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


def build_leaderboard_embed(
    title: str,
    window_text: str,
    rows: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
    viewer_id: int | None = None,
    viewer_rank: int | None = None,
    viewer_row: dict[str, Any] | None = None,
    guild_name: str | None = None,
    guild_icon_url: str | None = None,
) -> discord.Embed:
    """??lb — 3-column podium (2nd · 1st · 3rd side by side) with ranks 4+ listed below."""
    embed = discord.Embed(title=title, color=COLORS["gold"], timestamp=_now())
    if guild_name:
        embed.set_author(name=guild_name, icon_url=guild_icon_url)

    if not rows:
        embed.description = f"{window_text}\n\n🕊️ **No approved kills yet.**\nBe the first on the board!"
        return embed

    embed.description = window_text

    def column(rank: int) -> str:
        if rank > len(rows):
            return "—"
        r = rows[rank - 1]
        return f"<@{r['user_id']}>\n**{int(r['kills'])}** kills\n💰 {money(int(r['amount']))}"

    # Discord fills inline fields left to right, so 2nd · 1st · 3rd makes a podium.
    embed.add_field(name="🥈 2nd", value=column(2), inline=True)
    embed.add_field(name="🥇 1st", value=column(1), inline=True)
    embed.add_field(name="🥉 3rd", value=column(3), inline=True)

    rest = rows[3:]
    if rest:
        lines = [
            f"`{i:>2}.` <@{r['user_id']}> · **{int(r['kills'])}** kills · {money(int(r['amount']))}"
            for i, r in enumerate(rest, start=4)
        ]
        for n, chunk in enumerate(_split_field_lines(lines)):
            embed.add_field(name=f"📋 Rankings {4}–{3 + len(rest)}" if n == 0 else "\u200b", value=chunk, inline=False)

    if viewer_rank is not None and viewer_row is not None and viewer_rank > len(rows):
        embed.add_field(
            name="📍 Your position",
            value=f"**#{viewer_rank}** · {int(viewer_row['kills'])} kills · {money(int(viewer_row['amount']))}",
            inline=False,
        )
    embed.set_footer(text=_footer_for_viewer(_summary_text(rows, summary), viewer_id, viewer_rank))
    return embed


def build_top_embed(
    title: str,
    window_text: str,
    rows: list[dict[str, Any]],
    *,
    summary: dict[str, Any] | None = None,
    viewer_id: int | None = None,
    viewer_rank: int | None = None,
    viewer_row: dict[str, Any] | None = None,
    guild_name: str | None = None,
    guild_icon_url: str | None = None,
) -> discord.Embed:
    """??top — one clean line per player for the top 15."""
    embed = discord.Embed(title=title, color=COLORS["gold"], timestamp=_now())
    if guild_name:
        embed.set_author(name=guild_name, icon_url=guild_icon_url)
    if not rows:
        embed.description = f"{window_text}\n\n🕊️ **No approved kills yet.**"
        return embed

    lines = []
    for i, r in enumerate(rows, start=1):
        marker = MEDALS.get(i, f"`{i:>2}.`")
        lines.append(f"{marker} <@{r['user_id']}> — **{int(r['kills'])}** kills · {money(int(r['amount']))}")
    desc = f"{window_text}\n\n" + "\n".join(lines)
    if viewer_rank is not None and viewer_row is not None and viewer_rank > len(rows):
        desc += f"\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n📍 `{viewer_rank:>2}.` <@{viewer_id}> — **{int(viewer_row['kills'])}** kills · {money(int(viewer_row['amount']))}"
    embed.description = desc[:4096]
    embed.set_footer(text=_footer_for_viewer(_summary_text(rows, summary), viewer_id, viewer_rank))
    return embed


def build_me_embed(
    title: str,
    window_text: str,
    user_id: int,
    rows: list[dict[str, Any]],
    *,
    display_name: str = "Player",
    avatar_url: str | None = None,
    rank: int | None = None,
    players: int = 0,
    ahead: dict[str, Any] | None = None,
    behind: dict[str, Any] | None = None,
    leader: dict[str, Any] | None = None,
    pending: int = 0,
) -> discord.Embed:
    """??me — rival card: your numbers, who is ahead of you, who is behind you, progress to #1."""
    embed = discord.Embed(title=title, color=COLORS["player"], timestamp=_now())
    embed.set_author(name=display_name, icon_url=avatar_url)
    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    if not rows:
        embed.description = (
            f"{window_text}\n\n🕊️ **No approved kills in this window yet.**\n"
            "Post your screenshot in a target channel and it will show up here once it is accepted."
        )
        embed.add_field(name="🏅 Rank", value="—", inline=True)
    else:
        kills = sum(int(r["kills"]) for r in rows)
        amount = sum(int(r["amount"]) for r in rows)
        medal = MEDALS.get(rank or 0, "🏅")
        rank_text = f"{medal} Rank **#{rank}** of {players}" if rank and players else "🏅 Unranked"
        embed.description = f"{rank_text}\n{window_text}"
        embed.add_field(name="🎯 Kills", value=f"**{kills:,}**", inline=True)
        embed.add_field(name="💰 Earned", value=f"**{money(amount)}**", inline=True)

        # ----- rivals
        if ahead is not None:
            gap = int(ahead["kills"]) - kills
            gap_text = f"**{_kills_word(gap)}** more" if gap > 0 else "same kills, more money"
            embed.add_field(name="⬆️ Ahead of you", value=f"<@{ahead['user_id']}> · {gap_text}", inline=False)
        elif rank == 1:
            embed.add_field(name="👑 You are #1", value="Nobody is ahead of you. Keep the lead!", inline=False)
        if behind is not None:
            gap = kills - int(behind["kills"])
            gap_text = f"**{_kills_word(gap)}** fewer" if gap > 0 else "same kills, less money"
            embed.add_field(name="⬇️ Behind you", value=f"<@{behind['user_id']}> · {gap_text}", inline=False)

        # ----- progress to #1
        if leader is not None and int(leader["kills"]) > 0:
            pct = min(100, round(100 * kills / int(leader["kills"])))
            embed.add_field(
                name="📊 Progress to #1",
                value=f"{progress_bar(kills, int(leader['kills']))} **{pct}%**",
                inline=False,
            )

        # ----- per-target breakdown (only useful with more than one target)
        if len(rows) > 1:
            ordered = sorted(rows, key=lambda r: (-int(r["kills"]), r["target_name"].lower()))
            top = int(ordered[0]["kills"]) or 1
            lines = [
                f"**{r['target_name']}** {progress_bar(int(r['kills']), top, 8)} {int(r['kills'])} · {money(int(r['amount']))}"
                for r in ordered[:10]
            ]
            embed.add_field(name="📋 By target", value="\n".join(lines)[:1024], inline=False)

    if pending:
        embed.set_footer(text=f"⏳ {pending} claim(s) from this window are still pending review")
    else:
        embed.set_footer(text="Sunday 08:00 reset")
    return embed


def build_pending_embed(rows: list[dict[str, Any]]) -> discord.Embed:
    if not rows:
        return discord.Embed(
            title="⏳ Pending claims",
            description="No claims waiting for review.",
            color=COLORS["info"],
        )
    lines = []
    for r in rows:
        kills = r.get("total_kills", 0)
        jump = f" · [Jump]({r['jump_url']})" if r.get("jump_url") else ""
        lines.append(
            f"**#{r['id']}** · {r['target_name']} · {kills} kills · by <@{r['submitter_id']}> · <t:{r['created_at']}:R>{jump}"
        )
    return discord.Embed(
        title=f"⏳ Pending claims ({len(rows)})",
        description="\n".join(lines)[:4000],
        color=COLORS["pending"],
    )


def build_help_embed(prefix: str) -> discord.Embed:
    p = prefix
    e = discord.Embed(title="Kill tracker — commands", color=COLORS["info"])
    e.add_field(
        name="Setup (staff)",
        value=(
            f"`{p}review <name>` — creates a private channel; all claims go there for approval\n"
            f"`{p}target <name> [prices]` — make this channel a target\n"
            f"  e.g. `{p}target alpha` · `{p}target alpha 50000` · `{p}target alpha 50000,1000000`\n"
            f"`{p}untarget` · `{p}targets` — stop watching / list targets\n"
            f"`{p}reportchannel [#channel]` — where Sunday report is posted\n"
            f"`{p}reviewrole add|remove @role` — let a role approve claims\n"
            f"`{p}auditchannel [#channel]` — live Accept/Reject/Edit log channel\n"
            f"`{p}backup` — DM you the database file"
        ),
        inline=False,
    )
    e.add_field(
        name="Reports (staff)",
        value=(
            f"`{p}bonus [target|@member] [last]` — kills + money this week / last week\n"
            f"`{p}weekly` — last week's full report + Excel + reset summary\n"
            f"`{p}sheet [last]` — Excel only\n"
            f"`{p}midweek` — mid-week snapshot of current week\n"
            f"`{p}pending` — list claims waiting for review\n"
            f"`{p}testbonus [last]` — test run + preview Excel sheet (nothing is paid)\n"
            f"`{p}audit` — last 20 Accept/Reject/Edit actions\n"
            f"`{p}edit <claim #>` — edit a reviewed claim: kills, add/remove member, price per kill, or delete it (not counted)"
        ),
        inline=False,
    )
    e.add_field(
        name="Everyone",
        value=(
            f"`{p}me` / `{p}mykills` `[last]` — your rank card: kills, money, who is ahead / behind you\n"
            f"`{p}top` `[last]` — top 15 killers\n"
            f"`{p}lb` `[last]` — leaderboard podium (top 3 + ranks 4-15)"
        ),
        inline=False,
    )
    e.add_field(
        name="How claims work",
        value=(
            "In a target channel: tag member(s) + `5x`/`5k` + screenshot.\n"
            "Optional price in the message (`100k`, `1.5m`). Review role is tagged automatically.\n"
            "Max 5 claims per user per hour. Reject has quick reason templates."
        ),
        inline=False,
    )
    e.set_footer(text="Week = Sunday 08:00 → next Sunday 08:00 (TIMEZONE in .env)")
    return e

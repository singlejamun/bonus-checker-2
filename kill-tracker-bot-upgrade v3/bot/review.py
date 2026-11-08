"""Shared logic for the approval cards: posting, re-rendering and telling the original channel."""
from __future__ import annotations

import io
import logging
from pathlib import PurePath
from typing import TYPE_CHECKING

import discord

from .embeds import build_review_embed

if TYPE_CHECKING:  # pragma: no cover
    from .client import KillBot

log = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_SHOTS = 10


def _is_image(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.lower().startswith("image/"):
        return True
    return PurePath(att.filename).suffix.lower() in IMAGE_EXTS


async def collect_screenshots(
    message: discord.Message, size_budget: int
) -> tuple[list[discord.File], list[str], list[str]]:
    """Re-upload the screenshots so they stay visible even after Discord's CDN links expire.

    Returns (files, filenames, overflow_urls). overflow_urls are images we could not re-upload.
    """
    files: list[discord.File] = []
    names: list[str] = []
    overflow: list[str] = []
    used = 0
    for att in message.attachments:
        if not _is_image(att):
            continue
        if len(files) >= MAX_SHOTS or used + att.size > size_budget:
            overflow.append(att.url)
            continue
        try:
            data = await att.read()
        except discord.HTTPException:
            overflow.append(att.url)
            continue
        ext = PurePath(att.filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            ext = ".png"
        name = f"ss{len(files) + 1}{ext}"
        files.append(discord.File(io.BytesIO(data), filename=name))
        names.append(name)
        used += len(data)
    return files, names, overflow


async def render(bot: "KillBot", sid: int) -> tuple[dict, discord.Embed] | None:
    sub = await bot.db.get_submission(sid)
    if not sub:
        return None
    entries = await bot.db.get_entries(sid)
    return sub, build_review_embed(sub, entries)


async def refresh_review_message(bot: "KillBot", sid: int, *, remove_buttons: bool = False) -> None:
    """Re-draw the card in the review channel from the database."""
    rendered = await render(bot, sid)
    if not rendered:
        return
    sub, embed = rendered
    if not sub["review_channel_id"] or not sub["review_message_id"]:
        return
    try:
        channel = bot.get_channel(sub["review_channel_id"]) or await bot.fetch_channel(sub["review_channel_id"])
        msg = await channel.fetch_message(sub["review_message_id"])  # type: ignore[union-attr]
        if remove_buttons or sub["status"] != "pending":
            await msg.edit(embed=embed, view=None)
        else:
            await msg.edit(embed=embed)
    except discord.HTTPException:
        log.warning("Could not refresh review message for submission %s", sid, exc_info=True)


async def notify_source(bot: "KillBot", sub: dict, status: str, reason: str | None = None) -> None:
    """Mark the original message with ✅ / ❌ and, on rejection, reply with the reason."""
    try:
        channel = bot.get_channel(sub["source_channel_id"]) or await bot.fetch_channel(sub["source_channel_id"])
        msg = await channel.fetch_message(sub["source_message_id"])  # type: ignore[union-attr]
    except discord.HTTPException:
        return
    if bot.user:
        for old in ("⏳", "✅", "❌") if status == "deleted" else ("⏳",):
            try:
                await msg.remove_reaction(old, bot.user)
            except discord.HTTPException:
                pass
    try:
        await msg.add_reaction({"accepted": "✅", "deleted": "🗑️"}.get(status, "❌"))
        if status == "deleted":
            await msg.reply(f"🗑️ Claim #{sub['id']} was removed by staff and will **not** be counted.", mention_author=True)
        elif status == "rejected":
            text = f"❌ Claim #{sub['id']} was rejected."
            if reason:
                text += f"\n**Reason:** {reason[:1500]}"
            await msg.reply(text, mention_author=True)
    except discord.HTTPException:
        log.info("Could not notify source message for submission %s", sub["id"])


async def post_audit(bot: "KillBot", guild: discord.Guild, action: str, actor_id: int, detail: str) -> None:
    """Send a short audit line to the configured audit channel (if any)."""
    settings = await bot.db.get_settings(guild.id)
    if not settings or not settings.get("audit_channel_id"):
        return
    channel = guild.get_channel(settings["audit_channel_id"])
    if channel is None:
        return
    try:
        await channel.send(
            f"**{action}** by <@{actor_id}> · {detail}"[:1900],
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        log.debug("Could not post audit message in guild %s", guild.id)

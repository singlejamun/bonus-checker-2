"""Buttons, pop-ups (modals) and the edit panel for approval cards.

The three buttons are *dynamic items*: their custom_id carries the claim number, so they keep
working after the bot restarts.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord

from .checks import is_staff
from .embeds import build_edit_panel_embed
from .parsing import MAX_KILLS, parse_amount
from .review import notify_source, post_audit, refresh_review_message

if TYPE_CHECKING:  # pragma: no cover
    from .client import KillBot

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"(\d{15,25})")

REJECT_TEMPLATES = [
    ("Blurry screenshot", "Screenshot is too blurry / unreadable"),
    ("Wrong count", "Kill count does not match the screenshot"),
    ("Duplicate", "Duplicate claim — already submitted"),
    ("Not clear", "Screenshot does not clearly show the kills"),
    ("Wrong player", "Tagged player does not match the screenshot"),
    ("Custom…", None),  # opens modal
]


def decision_view(sid: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("accept", "reject", "edit"):
        view.add_item(DecisionButton(action, sid))
    return view


class DecisionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"kc:(?P<action>accept|reject|edit):(?P<sid>[0-9]+)",
):
    _LOOK = {
        "accept": ("Accept", discord.ButtonStyle.success, "✅"),
        "reject": ("Reject", discord.ButtonStyle.danger, "❌"),
        "edit": ("Edit", discord.ButtonStyle.secondary, "✏️"),
    }

    def __init__(self, action: str, sid: int) -> None:
        label, style, emoji = self._LOOK[action]
        super().__init__(
            discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=f"kc:{action}:{sid}")
        )
        self.action = action
        self.sid = sid

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str], /):
        return cls(match["action"], int(match["sid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_staff(interaction.client, interaction.user):  # type: ignore[arg-type]
            return True
        await interaction.response.send_message(
            "Only staff (Manage Server permission or a reviewer role) can review claims.", ephemeral=True
        )
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        sub = await bot.db.get_submission(self.sid)
        if not sub or sub["status"] != "pending":
            await interaction.response.send_message("This claim was already handled.", ephemeral=True)
            await refresh_review_message(bot, self.sid, remove_buttons=True)
            return

        if self.action == "accept":
            await self._accept(interaction, bot)
        elif self.action == "reject":
            view = RejectReasonView(self.sid)
            await interaction.response.send_message(
                "Pick a reject reason (or Custom):", view=view, ephemeral=True
            )
        else:
            embed, view = await build_panel(bot, interaction.guild, self.sid)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _accept(self, interaction: discord.Interaction, bot: "KillBot") -> None:
        ok = await bot.db.accept(self.sid, interaction.user.id)
        if not ok:
            await interaction.response.send_message(
                "Could not accept: the claim has no members left (use Edit to add one) or was already handled.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        await refresh_review_message(bot, self.sid, remove_buttons=True)
        sub = await bot.db.get_submission(self.sid)
        if sub:
            await notify_source(bot, sub, "accepted")
            entries = await bot.db.get_entries(self.sid)
            total = sum(int(e["kills"]) for e in entries)
            detail = f"Claim #{self.sid} · {sub['target_name']} · {total} kills"
            await bot.db.add_audit(sub["guild_id"], "accept", interaction.user.id, detail, self.sid)
            if interaction.guild:
                await post_audit(bot, interaction.guild, "Accept", interaction.user.id, detail)


class RejectReasonView(discord.ui.View):
    """Ephemeral view with template buttons + custom."""

    def __init__(self, sid: int) -> None:
        super().__init__(timeout=120)
        self.sid = sid
        for i, (label, _) in enumerate(REJECT_TEMPLATES):
            self.add_item(RejectTemplateButton(sid, i, label))


class RejectTemplateButton(discord.ui.Button):
    def __init__(self, sid: int, index: int, label: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=index // 3)
        self.sid = sid
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        label, reason = REJECT_TEMPLATES[self.index]
        if reason is None:
            await interaction.response.send_modal(RejectModal(self.sid))
            return
        await _do_reject(interaction, bot, self.sid, reason)


class RejectModal(discord.ui.Modal, title="Reject this claim"):
    def __init__(self, sid: int) -> None:
        super().__init__()
        self.sid = sid
        self.reason = discord.ui.TextInput(
            label="Why are you rejecting it?",
            style=discord.TextStyle.paragraph,
            placeholder="e.g. screenshot does not match the kill count",
            required=True,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        reason = str(self.reason.value).strip()
        await _do_reject(interaction, bot, self.sid, reason)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Reject modal failed", exc_info=error)
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong.", ephemeral=True)


async def _do_reject(interaction: discord.Interaction, bot: "KillBot", sid: int, reason: str) -> None:
    if not await bot.db.reject(sid, interaction.user.id, reason):
        await interaction.response.send_message("This claim was already handled.", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.send_message(f"Claim #{sid} rejected.", ephemeral=True)
    else:
        await interaction.followup.send(f"Claim #{sid} rejected.", ephemeral=True)
    await refresh_review_message(bot, sid, remove_buttons=True)
    sub = await bot.db.get_submission(sid)
    if sub:
        await notify_source(bot, sub, "rejected", reason)
        detail = f"Claim #{sid} · {sub['target_name']} · {reason[:200]}"
        await bot.db.add_audit(sub["guild_id"], "reject", interaction.user.id, detail, sid)
        if interaction.guild:
            await post_audit(bot, interaction.guild, "Reject", interaction.user.id, detail)


# --------------------------------------------------------------------------- edit panel
async def _display_name(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild else None
    return member.display_name[:60] if member else f"User {user_id}"


async def build_panel(bot: "KillBot", guild: discord.Guild | None, sid: int) -> tuple[discord.Embed, discord.ui.View]:
    sub = await bot.db.get_submission(sid)
    entries = await bot.db.get_entries(sid)
    embed = build_edit_panel_embed(sub, entries)  # type: ignore[arg-type]
    view = EditPanel(sid)
    if entries:
        options = [
            discord.SelectOption(
                label=await _display_name(guild, int(e["user_id"])),
                value=str(e["user_id"]),
                description=f"{e['kills']} kills",
            )
            for e in entries[:25]
        ]
        view.add_item(MemberSelect(sid, options))
    return embed, view


async def reply_with_panel(interaction: discord.Interaction, sid: int, note: str | None = None) -> None:
    """After a modal: update the panel the staff member is looking at."""
    bot: KillBot = interaction.client  # type: ignore[assignment]
    embed, view = await build_panel(bot, interaction.guild, sid)
    try:
        await interaction.response.edit_message(content=note, embed=embed, view=view)
    except (discord.HTTPException, TypeError):
        if not interaction.response.is_done():
            await interaction.response.send_message(content=note, embed=embed, view=view, ephemeral=True)


class MemberSelect(discord.ui.Select):
    def __init__(self, sid: int, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder="Pick a member to change kills (0 removes)", options=options, min_values=1, max_values=1)
        self.sid = sid

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        uid = int(self.values[0])
        current = next((e["kills"] for e in await bot.db.get_entries(self.sid) if int(e["user_id"]) == uid), 0)
        await interaction.response.send_modal(EditKillsModal(self.sid, uid, int(current)))


class EditPanel(discord.ui.View):
    def __init__(self, sid: int) -> None:
        super().__init__(timeout=600)
        self.sid = sid

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_staff(interaction.client, interaction.user):  # type: ignore[arg-type]
            return True
        await interaction.response.send_message(
            "Only staff (Manage Server permission or a reviewer role) can edit claims.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Add member", style=discord.ButtonStyle.primary, emoji="➕", row=1)
    async def add_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddMemberModal(self.sid))

    @discord.ui.button(label="Edit price", style=discord.ButtonStyle.secondary, emoji="💰", row=1)
    async def edit_price(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        sub = await bot.db.get_submission(self.sid)
        await interaction.response.send_modal(EditPriceModal(self.sid, int(sub["price"]) if sub else None))

    @discord.ui.button(label="Delete claim", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"Delete claim **#{self.sid}**? It will be removed from the bonus and **not counted**. "
            "This cannot be undone.",
            view=DeleteConfirmView(self.sid),
            ephemeral=True,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=1)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Editing finished.", embed=None, view=None)
        self.stop()


class DeleteConfirmView(discord.ui.View):
    def __init__(self, sid: int) -> None:
        super().__init__(timeout=60)
        self.sid = sid

    @discord.ui.button(label="Yes, delete it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        sub = await bot.db.get_submission(self.sid)
        was = sub["status"] if sub else "unknown"
        if not await bot.db.delete_claim(self.sid, interaction.user.id):
            await interaction.response.edit_message(
                content="This claim can't be deleted (it was already deleted or rejected).", view=None
            )
            return
        await interaction.response.edit_message(content=f"🗑️ Claim #{self.sid} deleted. It is no longer counted.", view=None)
        await refresh_review_message(bot, self.sid, remove_buttons=True)
        sub = await bot.db.get_submission(self.sid) or sub
        if sub:
            await notify_source(bot, sub, "deleted")
            detail = f"Claim #{self.sid} · {sub['target_name']} · deleted (was {was})"
            await bot.db.add_audit(sub["guild_id"], "delete", interaction.user.id, detail, self.sid)
            if interaction.guild:
                await post_audit(bot, interaction.guild, "Delete", interaction.user.id, detail)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled. Nothing was changed.", view=None)
        self.stop()


def _parse_kills(text: str) -> int | None:
    text = text.strip().lower().rstrip("xk").strip()
    if not text.isdigit():
        return None
    value = int(text)
    return value if 0 <= value <= MAX_KILLS else None


async def _apply(interaction: discord.Interaction, ok: bool, sid: int, note: str) -> None:
    bot: KillBot = interaction.client  # type: ignore[assignment]
    if not ok:
        await interaction.response.send_message(
            "This claim was rejected or deleted, so it can't be edited.", ephemeral=True
        )
        return
    await refresh_review_message(bot, sid)
    await reply_with_panel(interaction, sid, note)
    # audit edit
    sub = await bot.db.get_submission(sid)
    if sub and sub["status"] == "deleted":  # the last member was removed from an accepted claim
        await refresh_review_message(bot, sid, remove_buttons=True)
        await notify_source(bot, sub, "deleted")
        note += " (no members left, so the claim was deleted)"
    if sub and interaction.guild:
        await bot.db.add_audit(sub["guild_id"], "edit", interaction.user.id, f"Claim #{sid}: {note}", sid)
        await post_audit(bot, interaction.guild, "Edit", interaction.user.id, f"Claim #{sid}: {note}")


class EditKillsModal(discord.ui.Modal, title="Change kills"):
    def __init__(self, sid: int, user_id: int, current: int) -> None:
        super().__init__()
        self.sid, self.user_id = sid, user_id
        self.kills = discord.ui.TextInput(
            label="Kills (0 removes this member)", max_length=6, required=True, default=str(current)
        )
        self.add_item(self.kills)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        value = _parse_kills(str(self.kills.value))
        if value is None:
            await interaction.response.send_message(f"Enter a whole number from 0 to {MAX_KILLS}.", ephemeral=True)
            return
        ok = await bot.db.set_entry(self.sid, self.user_id, value, allow_accepted=True)
        note = f"Removed <@{self.user_id}>." if value == 0 else f"Set <@{self.user_id}> to {value} kills."
        await _apply(interaction, ok, self.sid, note)


class AddMemberModal(discord.ui.Modal, title="Add a member"):
    def __init__(self, sid: int) -> None:
        super().__init__()
        self.sid = sid
        self.member = discord.ui.TextInput(label="Member (@mention or user ID)", max_length=60, required=True)
        self.kills = discord.ui.TextInput(label="Kills to add", max_length=6, required=True)
        self.add_item(self.member)
        self.add_item(self.kills)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        m = _ID_RE.search(str(self.member.value))
        value = _parse_kills(str(self.kills.value))
        if not m or not value:
            await interaction.response.send_message(
                "Give a member (mention or ID) and kills from 1 to " f"{MAX_KILLS}.", ephemeral=True
            )
            return
        uid = int(m.group(1))
        guild = interaction.guild
        if guild is not None and guild.get_member(uid) is None:
            try:
                await guild.fetch_member(uid)
            except discord.NotFound:
                await interaction.response.send_message("That user is not in this server.", ephemeral=True)
                return
            except discord.HTTPException:
                pass  # can't verify; allow it
        existing = {int(e["user_id"]): int(e["kills"]) for e in await bot.db.get_entries(self.sid)}
        total = existing.get(uid, 0) + value
        ok = await bot.db.set_entry(self.sid, uid, min(total, MAX_KILLS), allow_accepted=True)
        await _apply(interaction, ok, self.sid, f"<@{uid}> now has {min(total, MAX_KILLS)} kills.")


class EditPriceModal(discord.ui.Modal, title="Price per kill"):
    def __init__(self, sid: int, current: int | None = None) -> None:
        super().__init__()
        self.sid = sid
        self.price = discord.ui.TextInput(
            label="Price per kill (e.g. 50000, 100k, 1.5m)", max_length=20, required=True,
            default=str(current) if current else None,
        )
        self.add_item(self.price)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: KillBot = interaction.client  # type: ignore[assignment]
        value = parse_amount(str(self.price.value))
        if value is None:
            await interaction.response.send_message("That is not a valid price. Try 50000, 100k or 1.5m.", ephemeral=True)
            return
        ok = await bot.db.set_price(self.sid, value, allow_accepted=True)
        await _apply(interaction, ok, self.sid, f"Price per kill set to {value:,}.")

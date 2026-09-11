"""Replies and moderation rules.

Two halves that always travel together: the Components V2 building blocks every
reply is made of, and the punishment plumbing (who may punish whom, recording
the case, the receipt panel) shared by ``ban``, ``kick`` and ``warn``.

A :class:`Panel` is a :class:`discord.ui.LayoutView` wrapping a single
``Container``. Send it with ``view=`` (never alongside ``embed=`` or
``content=`` — Components V2 messages carry neither).
"""

import contextlib
from collections.abc import Sequence

import discord
from discord import ui
from discord.ext import commands

from columbina.config import cf
from columbina.database import ACTIONS, Case, Database

FUCHSIA = discord.Color.fuchsia()
RED = discord.Color.red()
ORANGE = discord.Color.orange()

# Settings keys, as stored in the database.
LOG_CHANNEL = "log_channel"

# Every punishment is posted to the log channel in its own colour.
ACTION_COLORS: dict[str, discord.Color] = {
    "ban": RED,
    "kick": ORANGE,
    "warn": FUCHSIA,
}

# Discord rejects an audit-log reason longer than this.
REASON_LIMIT = 512
NO_REASON = "None given"

type Field = tuple[str, str]
type Media = str | discord.MediaGalleryItem


# --------------------------------------------------------------------------
# Components V2 building blocks
# --------------------------------------------------------------------------


def image(
    url: str, *, alt: str | None = None, spoiler: bool = False
) -> discord.MediaGalleryItem:
    """A gallery item with optional alt text / spoiler blur."""
    return discord.MediaGalleryItem(url, description=alt, spoiler=spoiler)


class Panel(ui.LayoutView):
    """A LayoutView holding one accent-barred container."""

    def __init__(self, box: ui.Container) -> None:
        super().__init__(timeout=None)
        self.add_item(box)


def heading(title: str, url: str | None = None) -> str:
    return f"## [{title}]({url})" if url else f"## {title}"


def renderFields(fields: list[Field]) -> str:
    return "\n\n".join(f"**{name}**\n{value}" for name, value in fields)


def linkButton(label: str, url: str, emoji: str | None = None) -> ui.Button:
    """A URL button — opens a link, has no callback, and never times out."""
    return ui.Button(label=label, url=url, emoji=emoji, style=discord.ButtonStyle.link)


def container(
    *,
    title: str | None = None,
    url: str | None = None,
    body: str | None = None,
    fields: list[Field] | None = None,
    thumbnail: str | None = None,
    images: Sequence[Media] | None = None,
    files: list[str] | None = None,
    buttons: list[ui.Button] | None = None,
    footer: str | None = None,
    color: discord.Color | int | None = FUCHSIA,
) -> ui.Container:
    """Build a Container from embed-shaped pieces, laid out the V2 way.

    ``images`` take URLs, ``attachment://name`` refs, or ``image()`` items
    (1-10, rendered as one gallery); ``files`` take ``attachment://name``
    refs. A V2 message hides any uploaded attachment it does not reference.
    ``buttons`` render as a row inside the box, above the footer.
    """
    lead = [part for part in (heading(title, url) if title else None, body) if part]
    leadText = "\n\n".join(lead)

    blocks: list[ui.Item] = []

    if thumbnail:
        # A Section needs at least one text child.
        blocks.append(
            ui.Section(
                ui.TextDisplay(leadText or "\u200b"),
                accessory=ui.Thumbnail(thumbnail),
            )
        )
    elif leadText:
        blocks.append(ui.TextDisplay(leadText))

    if fields:
        blocks.append(ui.TextDisplay(renderFields(fields)))

    if images:
        blocks.append(
            ui.MediaGallery(
                *(
                    item if isinstance(item, discord.MediaGalleryItem) else image(item)
                    for item in images[:10]
                )
            )
        )

    for ref in files or []:
        blocks.append(ui.File(ref))

    if buttons:
        row = ui.ActionRow()
        for btn in buttons:
            row.add_item(btn)
        blocks.append(row)

    if footer:
        blocks.append(ui.Separator(visible=False))
        blocks.append(ui.TextDisplay(f"-# {footer}"))

    if not blocks:
        blocks.append(ui.TextDisplay("\u200b"))

    return ui.Container(*blocks, accent_colour=color)


def panel(
    *,
    title: str | None = None,
    url: str | None = None,
    body: str | None = None,
    fields: list[Field] | None = None,
    thumbnail: str | None = None,
    images: Sequence[Media] | None = None,
    files: list[str] | None = None,
    buttons: list[ui.Button] | None = None,
    footer: str | None = None,
    color: discord.Color | int | None = FUCHSIA,
) -> Panel:
    return Panel(
        container(
            title=title,
            url=url,
            body=body,
            fields=fields,
            thumbnail=thumbnail,
            images=images,
            files=files,
            buttons=buttons,
            footer=footer,
            color=color,
        )
    )


def error(message: str) -> Panel:
    return panel(body=f":x: {message}", color=RED)


# --------------------------------------------------------------------------
# Punishments
# --------------------------------------------------------------------------


def snowflake(user: discord.Member | discord.User) -> discord.Object:
    """A bare ID reference to ``user``, for the REST calls that only need one.

    discord.py's ``Snowflake`` protocol declares a writable ``id``, but Member
    and User expose it as a read-only property, so no real user object ever
    satisfies it. An Object does, and ``ban``/``kick`` only ever wanted the ID.
    """
    return discord.Object(id=user.id)


def auditReason(moderator: discord.Member | discord.User, reason: str | None) -> str:
    """The reason as Discord's audit log will show it, moderator attached."""
    return f"{moderator} ({moderator.id}): {reason or NO_REASON}"[:REASON_LIMIT]


def refuse(
    ctx: commands.Context,
    user: discord.Member | discord.User,
    *,
    action: str,
    requireMember: bool = False,
    requireRank: bool = True,
) -> str | None:
    """Why this punishment shouldn't go through, or ``None`` if it should.

    Discord would reject most of these with a bare 403; checking first lets us
    say which of them it was. ``requireRank`` is for punishments Discord
    actually enforces a hierarchy on — a warning is just a database row, so it
    doesn't care whether *she* out-ranks the target, only that the mod does.
    """
    guild = ctx.guild
    if guild is None:  # guild_only() already guarantees this; narrows the type.
        return "That one only works in a server."

    if user.id == ctx.author.id:
        return f"You can't {action} yourself."
    if user.id == guild.me.id:
        return f"I'm not going to {action} myself."
    if user.id == guild.owner_id:
        return f"I can't {action} the server owner."

    member = guild.get_member(user.id)
    if member is None:
        # Not in the server. Nothing to out-rank, so a pre-emptive ban is fine,
        # but there's nobody here to kick.
        return "They aren't in the server." if requireMember else None

    if requireRank and guild.me.top_role <= member.top_role:
        return (
            f"{member.mention}'s highest role isn't below mine, so I can't touch them."
        )
    if ctx.author != guild.owner and (
        not isinstance(ctx.author, discord.Member)
        or ctx.author.top_role <= member.top_role
    ):
        return f"{member.mention}'s highest role isn't below yours."
    return None


def caseLog(
    case: Case,
    user: discord.Member | discord.User,
    moderator: discord.Member | discord.User,
) -> Panel:
    """The panel posted to the log channel when a punishment lands."""
    past = ACTIONS[case.action]
    when = int(case.createdAt.timestamp())
    return panel(
        title=f"{past.title()}: {user}",
        fields=[
            ("User", f"{user.mention} • `{user.id}`"),
            ("Moderator", f"{moderator.mention} • `{moderator.id}`"),
            ("Reason", case.reason or NO_REASON),
            ("When", f"<t:{when}:F>"),
        ],
        thumbnail=user.display_avatar.url,
        footer=f"case {case.hash}",
        color=ACTION_COLORS.get(case.action, FUCHSIA),
    )


async def postToLog(db: Database, guild: discord.Guild, entry: Panel) -> None:
    """Post a panel to the log channel, if one is set and reachable.

    Logging must never be the thing that breaks a moderation action, so every
    failure here is swallowed after a note to the console — the ban already
    happened, and losing the receipt is better than losing the reply.
    """
    raw = await db.setting(guild.id, LOG_CHANNEL)
    if raw is None:
        return

    channel = guild.get_channel(int(raw))
    if not isinstance(channel, discord.TextChannel):
        print(cf.red(f"[logs] channel {raw} is gone; run `logs set` again"))
        return

    if not channel.permissions_for(guild.me).send_messages:
        print(cf.red(f"[logs] I can't post in #{channel}"))
        return

    with contextlib.suppress(discord.HTTPException):
        await channel.send(view=entry)


async def announce(
    db: Database,
    guild: discord.Guild,
    case: Case,
    user: discord.Member | discord.User,
    moderator: discord.Member | discord.User,
) -> None:
    """Post a punishment to the log channel."""
    await postToLog(db, guild, caseLog(case, user, moderator))


async def record(
    db: Database,
    guild: discord.Guild,
    user: discord.Member | discord.User,
    moderator: discord.Member | discord.User,
    *,
    action: str,
    reason: str | None,
) -> Case:
    """Store the punishment and post it to the logs. Returns the case.

    Every punishment goes through here — commands and the honeypot alike — so
    the log channel can't fall out of step with what she actually did.
    """
    case = await db.addCase(
        guildId=guild.id,
        userId=user.id,
        moderatorId=moderator.id,
        action=action,
        reason=reason,
    )
    await announce(db, guild, case, user, moderator)
    return case


def receipt(
    ctx: commands.Context,
    user: discord.Member | discord.User,
    case: Case,
    *,
    extra: list[Field] | None = None,
) -> Panel:
    """The panel a mod sees after a punishment lands."""
    past = ACTIONS[case.action]
    fields: list[Field] = [
        ("Reason", case.reason or NO_REASON),
        ("Case", f"`{case.hash}`"),
    ]
    return panel(
        title=past.title(),
        body=f"{user.mention} has been {past}.",
        fields=fields + (extra or []),
        thumbnail=user.display_avatar.url,
        footer=f"{past.title()} by {ctx.author} • case {case.hash}",
    )

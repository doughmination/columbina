"""Honeypot channels: a trap nobody legitimate should ever post in.

Lock a channel so members can read but not send, leave it looking inviting, and
the self-invited spam accounts that ignore permissions and post anyway ban
themselves. Staff are exempt, so you can walk someone through it safely.
"""

import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot
from columbina.config import cf

# How much of a caught account's recent history to wipe. Discord caps this at
# 7 days; a day is enough to clear the spam run that got them caught.
PURGE_SECONDS = 24 * 60 * 60


class Honeypot(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        # Mirrors the honeypots table, so the listener doesn't hit the database
        # on every single message. Kept in step by the commands below.
        self.trapped: set[int] = set()

    async def cog_load(self) -> None:
        self.trapped = await self.bot.db.allHoneypots()
        if self.trapped:
            print(cf.cyan(f"[honeypot] watching {len(self.trapped)} channel(s)"))

    @commands.hybrid_group(
        name="honeypot",
        description="Manage trap channels",
        fallback="list",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def honeypot(self, ctx: commands.Context) -> None:
        """List the trap channels."""
        assert ctx.guild is not None
        channels = await self.bot.db.honeypots(ctx.guild.id)
        if not channels:
            await ctx.send(
                view=moderation.panel(
                    title="Honeypots",
                    body="No traps set. Use `honeypot set #channel` to lay one.",
                )
            )
            return

        await ctx.send(
            view=moderation.panel(
                title="Honeypots",
                body="\n".join(f"<#{channel}>" for channel in channels),
                footer="Anyone who posts in these is banned on sight",
            )
        )

    # The checks are repeated on each subcommand deliberately: the group's own
    # checks don't reliably reach the slash-command path of a hybrid group.
    @honeypot.command(name="set", description="Trap a channel")
    @app_commands.describe(channel="The channel to turn into a trap")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setChannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        assert ctx.guild is not None
        if channel.guild.id != ctx.guild.id:
            await ctx.send(view=moderation.error("That channel isn't in this server."))
            return

        added = await self.bot.db.addHoneypot(ctx.guild.id, channel.id, ctx.author.id)
        if not added:
            await ctx.send(
                view=moderation.error(f"{channel.mention} is already a honeypot.")
            )
            return

        self.trapped.add(channel.id)
        await ctx.send(
            view=moderation.panel(
                title="Trap laid",
                body=f"Anyone who posts in {channel.mention} will be banned.",
                fields=[
                    (
                        "Reminder",
                        (
                            "Deny **Send Messages** there for @everyone, so only"
                            " the accounts that shouldn't be able to post will."
                        ),
                    )
                ],
                footer=f"Set by {ctx.author}",
            )
        )

    @honeypot.command(name="clear", description="Stop trapping a channel")
    @app_commands.describe(channel="The channel to stop trapping")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def clearChannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        assert ctx.guild is not None
        removed = await self.bot.db.removeHoneypot(ctx.guild.id, channel.id)
        self.trapped.discard(channel.id)
        if not removed:
            await ctx.send(
                view=moderation.error(f"{channel.mention} wasn't a honeypot.")
            )
            return

        await ctx.send(
            view=moderation.panel(
                title="Trap cleared",
                body=f"{channel.mention} is an ordinary channel again.",
                footer=f"Cleared by {ctx.author}",
            )
        )

    def exempt(self, member: discord.Member) -> bool:
        """Staff and bots trip no traps — someone has to be able to test it."""
        perms = member.guild_permissions
        return (
            member.bot
            or member.id == member.guild.owner_id
            or perms.administrator
            or perms.manage_guild
            or perms.ban_members
            or perms.moderate_members
            or perms.manage_messages
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.channel.id not in self.trapped:
            return

        author = message.author
        # A webhook or a departed member arrives as a User, not a Member.
        if not isinstance(author, discord.Member) or self.exempt(author):
            return

        guild = message.guild
        reason = f"Posted in honeypot channel #{message.channel}"

        if not guild.me.guild_permissions.ban_members:
            print(cf.red(f"[honeypot] caught {author} but I can't ban — no permission"))
            return
        if guild.me.top_role <= author.top_role:
            print(cf.red(f"[honeypot] caught {author} but they out-rank me"))
            return

        try:
            await author.ban(reason=reason, delete_message_seconds=PURGE_SECONDS)
        except discord.HTTPException as e:
            print(cf.red(f"[honeypot] failed to ban {author}: {e}"))
            return

        # Through record() rather than straight to the database, so an
        # automatic ban shows up in the log channel like any other.
        case = await moderation.record(
            self.bot.db, guild, author, guild.me, action="ban", reason=reason
        )
        print(
            cf.magenta(f"[honeypot] banned {author} ({author.id}) — case {case.hash}")
        )


async def setup(bot: Bot) -> None:
    await bot.add_cog(Honeypot(bot))

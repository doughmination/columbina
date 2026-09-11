"""The log channel: where every punishment gets written down.

Nothing posts here directly. Punishments funnel through ``moderation.record``,
which announces them — so a command can't do something without it being logged.
"""

import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot


class Logs(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.hybrid_group(
        name="logs",
        description="Where punishments get logged",
        fallback="show",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def logs(self, ctx: commands.Context) -> None:
        """Show the current log channel."""
        assert ctx.guild is not None
        raw = await self.bot.db.setting(ctx.guild.id, moderation.LOG_CHANNEL)
        if raw is None:
            await ctx.send(
                view=moderation.panel(
                    title="Logs",
                    body="Nothing is being logged. Use `logs set #channel`.",
                )
            )
            return

        channel = ctx.guild.get_channel(int(raw))
        if channel is None:
            await ctx.send(
                view=moderation.error(
                    f"The log channel (`{raw}`) is gone. Set a new one."
                )
            )
            return

        await ctx.send(
            view=moderation.panel(
                title="Logs",
                body=f"Punishments are logged to {channel.mention}.",
                footer="Bans, kicks, warns, and anything the honeypot catches",
            )
        )

    # Checks repeated per subcommand: the group's own don't reliably reach the
    # slash-command path of a hybrid group.
    @logs.command(name="set", description="Set the channel punishments are logged to")
    @app_commands.describe(channel="Where to send the log")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setChannel(
        self, ctx: commands.Context, channel: discord.TextChannel
    ) -> None:
        assert ctx.guild is not None
        if channel.guild.id != ctx.guild.id:
            await ctx.send(view=moderation.error("That channel isn't in this server."))
            return

        # Better to find out now than to lose the first ban she tries to log.
        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.view_channel:
            await ctx.send(view=moderation.error(f"I can't post in {channel.mention}."))
            return

        await self.bot.db.setSetting(
            ctx.guild.id, moderation.LOG_CHANNEL, str(channel.id)
        )
        await ctx.send(
            view=moderation.panel(
                title="Logs set",
                body=f"Punishments will be logged to {channel.mention}.",
                footer=f"Set by {ctx.author}",
            )
        )

    @logs.command(name="clear", description="Stop logging punishments")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def clearChannel(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        cleared = await self.bot.db.clearSetting(ctx.guild.id, moderation.LOG_CHANNEL)
        if not cleared:
            await ctx.send(view=moderation.error("There was no log channel set."))
            return

        await ctx.send(
            view=moderation.panel(
                title="Logs cleared",
                body="Punishments won't be logged anywhere.",
                footer=f"Cleared by {ctx.author}",
            )
        )


async def setup(bot: Bot) -> None:
    await bot.add_cog(Logs(bot))

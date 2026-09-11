"""Bulk message deletion."""

import contextlib

import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot

# Discord's bulk-delete endpoint takes 100 messages at a time.
MAX = 100

# How long the confirmation sticks around after a prefix invocation. A purge is
# usually about tidying the channel, so the receipt shouldn't outstay the mess.
RECEIPT_SECONDS = 10.0


class Purge(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="purge",
        description="Delete up to 100 messages in this channel",
        aliases=["clear"],
    )
    @app_commands.describe(amount=f"How many messages to delete (1-{MAX})")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(
        self, ctx: commands.Context, amount: commands.Range[int, 1, MAX]
    ) -> None:
        channel = ctx.channel
        if not isinstance(
            channel,
            discord.TextChannel
            | discord.VoiceChannel
            | discord.StageChannel
            | discord.Thread,
        ):
            await ctx.send(view=moderation.error("I can't purge this kind of channel."))
            return

        if ctx.interaction is None:
            # The prefix invocation is itself a message in this channel. Clear it
            # first so it doesn't eat one of the messages they asked for.
            with contextlib.suppress(discord.HTTPException):
                await ctx.message.delete()
        else:
            await ctx.defer(ephemeral=True)

        try:
            deleted = await channel.purge(
                limit=amount,
                reason=moderation.auditReason(ctx.author, f"purged {amount}"),
            )
        except discord.HTTPException as e:
            await ctx.send(
                view=moderation.error(f"Couldn't purge: {e.text or e}"),
                ephemeral=True,
            )
            return

        assert ctx.guild is not None  # guild_only() saw to this
        await moderation.postToLog(
            self.bot.db,
            ctx.guild,
            moderation.panel(
                title="Messages purged",
                fields=[
                    ("Channel", channel.mention),
                    ("Moderator", f"{ctx.author.mention} • `{ctx.author.id}`"),
                    ("Deleted", f"{len(deleted)} message(s)"),
                ],
                color=moderation.ORANGE,
            ),
        )

        # Fewer than asked for means the channel ran out, not a failure.
        body = f"Deleted {len(deleted)} message(s)."
        if len(deleted) < amount:
            body += f" That was everything — there weren't {amount}."

        receipt = moderation.panel(title="Purged", body=body, color=moderation.ORANGE)
        if ctx.interaction is None:
            # Nobody else needs to see the receipt for a tidy-up.
            await ctx.send(view=receipt, delete_after=RECEIPT_SECONDS)
        else:
            await ctx.send(view=receipt, ephemeral=True)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Purge(bot))

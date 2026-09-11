import contextlib

import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot


class Warn(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="warn", description="Warn a user")
    @app_commands.describe(user="The user to warn", reason="The reason why")
    @commands.guild_only()
    @commands.has_permissions(moderate_members=True)
    async def warn(
        self,
        ctx: commands.Context,
        user: discord.Member | discord.User,
        reason: str | None = None,
    ) -> None:
        await ctx.defer()

        # A warning is a database row, not a Discord action — she doesn't need
        # to out-rank the target to write one down, only the moderator does.
        refusal = moderation.refuse(ctx, user, action="warn", requireRank=False)
        if refusal is not None:
            await ctx.send(view=moderation.error(refusal))
            return

        assert ctx.guild is not None  # guild_only() and refuse() both saw to this
        case = await moderation.record(
            self.bot.db, ctx.guild, user, ctx.author, action="warn", reason=reason
        )
        total = await self.bot.db.countCases(ctx.guild.id, user.id, "warn")

        await self.notify(ctx, user, reason, total)
        await ctx.send(
            view=moderation.receipt(
                ctx,
                user,
                case,
                extra=[("Warnings", f"{total} in total")],
            )
        )

    async def notify(
        self,
        ctx: commands.Context,
        user: discord.Member | discord.User,
        reason: str | None,
        total: int,
    ) -> None:
        """Tell the user off in DMs. They may have DMs shut; that's their right."""
        assert ctx.guild is not None
        with contextlib.suppress(discord.HTTPException):
            await user.send(
                view=moderation.panel(
                    title="Warning",
                    body=f"You've been warned in **{ctx.guild.name}**.",
                    fields=[
                        ("Reason", reason or moderation.NO_REASON),
                        ("Warnings", f"{total} in total"),
                    ],
                    color=moderation.RED,
                )
            )


async def setup(bot: Bot) -> None:
    await bot.add_cog(Warn(bot))

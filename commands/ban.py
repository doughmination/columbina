import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot


class Ban(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="ban", description="Ban a user", aliases=["yeet"])
    @app_commands.describe(user="The user to ban", reason="The reason why")
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.Member | discord.User,
        reason: str | None = None,
    ) -> None:
        await ctx.defer()

        refusal = moderation.refuse(ctx, user, action="ban")
        if refusal is not None:
            await ctx.send(view=moderation.error(refusal))
            return

        assert ctx.guild is not None  # guild_only() and refuse() both saw to this
        await ctx.guild.ban(
            moderation.snowflake(user),
            reason=moderation.auditReason(ctx.author, reason),
        )

        case = await moderation.record(
            self.bot.db, ctx.guild, user, ctx.author, action="ban", reason=reason
        )
        await ctx.send(view=moderation.receipt(ctx, user, case))


async def setup(bot: Bot) -> None:
    await bot.add_cog(Ban(bot))

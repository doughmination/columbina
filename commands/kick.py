import discord
from discord import app_commands
from discord.ext import commands

from columbina import moderation
from columbina.client import Bot


class Kick(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="kick", description="Kick a member")
    @app_commands.describe(user="The member to kick", reason="The reason why")
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        ctx: commands.Context,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        await ctx.defer()

        # Unlike a ban, there's no kicking someone who isn't here.
        refusal = moderation.refuse(ctx, user, action="kick", requireMember=True)
        if refusal is not None:
            await ctx.send(view=moderation.error(refusal))
            return

        assert ctx.guild is not None  # guild_only() and refuse() both saw to this
        await ctx.guild.kick(
            moderation.snowflake(user),
            reason=moderation.auditReason(ctx.author, reason),
        )

        case = await moderation.record(
            self.bot.db, ctx.guild, user, ctx.author, action="kick", reason=reason
        )
        await ctx.send(view=moderation.receipt(ctx, user, case))


async def setup(bot: Bot) -> None:
    await bot.add_cog(Kick(bot))

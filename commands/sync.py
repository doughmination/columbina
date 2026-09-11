"""Manual command syncing.

Prefix-only on purpose: if the slash commands are missing, a slash command is
no use for fixing them. She syncs on start-up anyway — this is for when you've
renamed something and don't want to restart, or need to sync globally.
"""

from discord.ext import commands

from columbina import moderation
from columbina.client import Bot


class Sync(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context, scope: str | None = None) -> None:
        """``sync`` for this server, ``sync global``, or ``sync clear``."""
        async with ctx.typing():
            match (scope or "guild").lower():
                case "guild" | "here":
                    count = await self.bot.syncCommands(
                        ctx.guild.id if ctx.guild else None
                    )
                    body = f"{count} command(s) synced to this server."
                case "global":
                    synced = await self.bot.tree.sync()
                    body = (
                        f"{len(synced)} command(s) synced globally."
                        " Clients can take up to an hour to catch up."
                    )
                case "clear":
                    if ctx.guild is None:
                        await ctx.send(
                            view=moderation.error("Run that one in the server.")
                        )
                        return
                    self.bot.tree.clear_commands(guild=ctx.guild)
                    await self.bot.tree.sync(guild=ctx.guild)
                    body = "Cleared this server's commands."
                case other:
                    await ctx.send(
                        view=moderation.error(
                            f"`{other}`? I know `guild`, `global` and `clear`."
                        )
                    )
                    return

        await ctx.send(view=moderation.panel(title="Sync", body=body))


async def setup(bot: Bot) -> None:
    await bot.add_cog(Sync(bot))

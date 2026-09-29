import discord
from discord.ext import commands

from columbina import client, config

class Events(commands.Cog):
    def __init__(self, bot: client.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        role = member.guild.get_role(config.joinRoleID)
        botRole = member.guild.get_role(config.botRoleID)

        if member.bot:
            if botRole is None:
                print(f"[join] Could not find join role {config.botRoleID}")
                return
            await member.add_roles(botRole)
            return

        if role is None:
            print(f"[join] Could not find join role {config.joinRoleID}")
            return
        await member.add_roles(role)
        return

async def setup(bot: Bot) -> None:
    await bot.add_cog(Events(bot))
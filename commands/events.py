import discord
from discord import ui as a
from discord.ext import commands

from columbina import client, config

class Events(commands.Cog):
    def __init__(self, bot: client.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:        
        try: 
            await self.memberRole(member)
        except discord.Forbidden or discord.HTTPException as e:
            print(f"[roles] There was an error {e}")
        
        try:
            await self.welcomeMessage(member)
        except discord.Forbidden or discord.HTTPException as f:
            print(f"[welcome] There was an error {f}")

    async def memberRole(self, member: discord.Member) -> None:
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
    
    async def welcomeMessage(self, member: discord.Member) -> None:
        channel = self.bot.get_channel(config.welcomeChannelId) or None
        if channel is None:
            print("[welcome] welcomeChannelId is not set!")
            return

        component = a.LayoutView ().add_item(
            a.Container(
                a.Section(
                    a.TextDisplay(content=f"# Welcome to Doughmination\n{member.mention}"),
                    accessory=a.Thumbnail(media=member.display_avatar.url),
                ),
                a.ActionRow(
                    a.Button(style=discord.ButtonStyle.link, label="Read the rules", url="https://discord.com/channels/1522105591739187262/1524763481948295270"),
                    a.Button(style=discord.ButtonStyle.blurple, label="Placeholder", disabled=True)
                ),
                a.TextDisplay(content="-# Any questions, feel free to ping <@1025770042245251122>"),
                accent_color=discord.Color.orange()
            )
        )
        await channel.send(view=component, allowed_mentions=discord.AllowedMentions.none())
        return

async def setup(bot: client.Bot) -> None:
    await bot.add_cog(Events(bot))
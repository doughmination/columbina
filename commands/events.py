import discord
from discord import ui as a
from discord.ext import commands

from columbina import client, config

# Message people react on, and which custom emoji ID gives which role ID.
reactionRoleMessageID = 1554891516265365627
reactionRoleMap = {
    1554893523352944702: 1554822616290693150,
    1554893476129407037: 1554822139088081006,
    1554893503195250748: 1554824677678120960,
}

class Events(commands.Cog):
    def __init__(self, bot: client.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:        
        try: 
            await self.joinRole(member)
        except discord.HTTPException as e:
            print(f"[roles] There was an error {e}")
        
        try:
            await self.welcomeMessage(member)
        except discord.HTTPException as f:
            print(f"[welcome] There was an error {f}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.message_id != reactionRoleMessageID:
            return
        if payload.member is None or payload.member.bot:
            return

        try:
            await self.reactionRoles(payload.emoji, payload.member)
        except discord.HTTPException as e:
            print(f"[reactionRoles] There was an error {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.message_id != reactionRoleMessageID or payload.guild_id is None:
            return

        # payload.member is always None on remove, so look them up.
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if member is None or member.bot:
            return

        try:
            await self.removeReactionRoles(payload.emoji, member)
        except discord.HTTPException as e:
            print(f"[reactionRoles] There was an error {e}")

    async def reactionRoles(self, emoji: discord.PartialEmoji, member: discord.Member) -> None:
        roleID = reactionRoleMap.get(emoji.id)
        if roleID is None:
            return
        await member.add_roles(discord.Object(id=roleID))

    async def removeReactionRoles(self, emoji: discord.PartialEmoji, member: discord.Member) -> None:
        roleID = reactionRoleMap.get(emoji.id)
        if roleID is None:
            return
        await member.remove_roles(discord.Object(id=roleID))

    async def joinRole(self, member: discord.Member) -> None:
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
        channel = self.bot.get_channel(config.welcomeChannelId)
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
import discord
from columbina import client, config

@client.Bot.event
async def on_member_join(member: discord.Member) -> None:
    role = member.guild.get_role(config.joinRoleID)
    botRole = member.guild.get_role(config.botRoleID)

    if member.bot:
        if botRole is None:
            print(f"[join] Could not find join role {config.botRoleID}")
            return

        await member.add_roles(botRole)

    if role is None:
        print(f"[join] Could not find join role {config.joinRoleID}")
        return

    await member.add_roles(role)
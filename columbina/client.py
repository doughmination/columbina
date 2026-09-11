"""The bot itself: prefixes, extension loading, error handling, lifecycle.

Everything about *running* her lives here. The pieces are separated by banner
comments rather than by files — there is one bot, and this is it.
"""

import asyncio
import contextlib
import signal
import sys
import traceback
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from columbina import config, moderation
from columbina.config import cf
from columbina.database import Database

# --------------------------------------------------------------------------
# Prefixes
# --------------------------------------------------------------------------

# Characters allowed between the name and the command word. Whatever is matched
# is folded into the returned prefix so discord.py strips it for us.
SEPARATORS = " \t\n,:;"


def matchName(content: str) -> str | None:
    """The literal prefix slice of ``content``, or ``None`` if no name matched.

    The slice is returned verbatim (not lowercased) because discord.py compares
    prefixes against the raw message content, case-sensitively.
    """
    lowered = content.lower()
    for name in config.prefixNames:
        if not lowered.startswith(name):
            continue

        rest = content[len(name) :]
        if not rest:
            return None  # Just her name, no command behind it.

        stripped = rest.lstrip(SEPARATORS)
        if stripped == rest:
            continue  # "Marionettes", not "Marionette ..." — keep looking.
        if not stripped:
            return None  # Name plus trailing punctuation, still no command.

        return content[: len(content) - len(stripped)]
    return None


def resolvePrefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    prefixes = commands.when_mentioned(bot, message)
    matched = matchName(message.content)
    if matched is not None:
        prefixes.append(matched)
    return prefixes


# --------------------------------------------------------------------------
# Extension loading
# --------------------------------------------------------------------------


def discoverExtensions(directory: Path, package: str) -> list[str]:
    """Dotted extension names for every module under ``directory``.

    Recurses, so ``commands/cogs/mod.py`` becomes ``commands.cogs.mod``.
    Modules whose name starts with an underscore (``__init__`` included) are
    skipped — they are package plumbing, not extensions.
    """
    if not directory.is_dir():
        return []

    return sorted(
        ".".join((package, *path.relative_to(directory).with_suffix("").parts))
        for path in directory.rglob("*.py")
        if not path.stem.startswith("_")
    )


async def loadExtensions(bot: commands.Bot) -> tuple[list[str], list[str]]:
    """Load every discovered extension. Returns ``(loaded, failed)``.

    One broken extension does not take the rest down with it; its traceback is
    printed and the loader moves on.
    """
    loaded: list[str] = []
    failed: list[str] = []

    for name in discoverExtensions(config.commandsDir, config.commandsDir.name):
        try:
            await bot.load_extension(name)
        except commands.ExtensionError:
            failed.append(name)
            print(cf.red(f"[loader] {name} failed to load:"))
            traceback.print_exc()
        else:
            loaded.append(name)
            print(cf.green(f"[loader] loaded {name}"))

    summary = f"[loader] {len(loaded)} extension(s) loaded"
    if failed:
        summary += f", {len(failed)} failed: {', '.join(failed)}"
        print(cf.red(summary))
    else:
        print(cf.grey(summary))

    return loaded, failed


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def prettyPerms(perms: list[str]) -> str:
    """``['ban_members']`` -> ``'Ban Members'``."""
    return ", ".join(
        perm.replace("_", " ").replace("guild", "server").title() for perm in perms
    )


def describe(error: Exception) -> str | None:
    """A user-facing line for ``error``, or ``None`` if it is unexpected.

    ``None`` is the signal to log a traceback — it means we have no better
    explanation than "something broke".
    """
    match error:
        case commands.MissingRequiredArgument():
            return f"You need to tell me a `{error.param.name}` too."
        case commands.MemberNotFound() | commands.UserNotFound():
            return f"I couldn't find `{error.argument}`."
        case commands.RoleNotFound() | commands.ChannelNotFound():
            return f"I couldn't find `{error.argument}`."
        case commands.BadUnionArgument():
            return f"I couldn't make sense of `{error.param.name}`."
        case commands.RangeError():
            return f"`{error.value}` is out of range."
        case commands.TooManyArguments():
            return "That's more arguments than I know what to do with."
        case commands.BadArgument() | commands.UserInputError():
            return str(error) or "I couldn't make sense of that."
        case commands.MissingPermissions() | app_commands.MissingPermissions():
            return f"You need **{prettyPerms(error.missing_permissions)}** to do that."
        case commands.BotMissingPermissions() | app_commands.BotMissingPermissions():
            return f"I need **{prettyPerms(error.missing_permissions)}** to do that."
        case commands.NotOwner():
            return "That one's owner-only, sorry."
        case commands.NoPrivateMessage() | app_commands.NoPrivateMessage():
            return "That one only works in a server."
        case commands.PrivateMessageOnly():
            return "That one only works in DMs."
        case commands.CommandOnCooldown() | app_commands.CommandOnCooldown():
            return f"Slow down — try again in {error.retry_after:.1f}s."
        case commands.MaxConcurrencyReached():
            return "That command is already running; wait for it to finish."
        case commands.DisabledCommand():
            return "That command is disabled right now."
        case discord.Forbidden():
            return "Discord won't let me do that — check my role and permissions."
        case discord.HTTPException():
            return f"Discord turned that down: {error.text or error}"
        case commands.CheckFailure() | app_commands.CheckFailure():
            return "You can't use that one here."
        case _:
            return None


def logError(name: str, error: BaseException) -> None:
    print(cf.red(f"[error] {name} raised {type(error).__name__}:"))
    traceback.print_exception(type(error), error, error.__traceback__)


def unwrap(error: Exception) -> Exception:
    """Peel off the wrapper discord.py puts around errors raised inside a command."""
    while isinstance(
        error,
        commands.CommandInvokeError
        | commands.HybridCommandError
        | commands.ConversionError
        | app_commands.CommandInvokeError
        | app_commands.TransformerError,
    ):
        original = getattr(error, "original", None) or getattr(error, "__cause__", None)
        if not isinstance(original, Exception) or original is error:
            break
        error = original
    return error


async def handleCommandError(
    ctx: commands.Context, error: commands.CommandError
) -> None:
    # Her name is a prefix, so "columbina hi" reaches us as CommandNotFound.
    # Treating that as an error would make her scold every casual mention.
    if isinstance(error, commands.CommandNotFound):
        return

    if ctx.command is not None and ctx.command.has_error_handler():
        return

    actual = unwrap(error)
    message = describe(actual)
    if message is None:
        logError(
            ctx.command.qualified_name if ctx.command else "unknown command", actual
        )
        message = "Something went wrong on my end. It's been logged."

    try:
        await ctx.send(view=moderation.error(message), ephemeral=True)
    except discord.HTTPException:
        pass


async def handleAppCommandError(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    actual = unwrap(error)
    message = describe(actual)
    if message is None:
        name = (
            interaction.command.qualified_name
            if interaction.command
            else "unknown command"
        )
        logError(name, actual)
        message = "Something went wrong on my end. It's been logged."

    panel = moderation.error(message)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(view=panel, ephemeral=True)
        else:
            await interaction.response.send_message(view=panel, ephemeral=True)
    except discord.HTTPException:
        pass


# --------------------------------------------------------------------------
# The bot
# --------------------------------------------------------------------------


class Bot(commands.Bot):
    db: Database

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._profileSet = False
        self._synced = False

    async def setup_hook(self) -> None:
        """Runs once after login, before the first ready — open up and load in."""
        self.tree.error(handleAppCommandError)
        self.db = await Database.connect(config.databasePath)
        print(cf.cyan(f"[db] opened {self.db.path}"))
        await loadExtensions(self)

    async def close(self) -> None:
        """Shut the database after the connection to Discord goes."""
        try:
            await super().close()
        finally:
            db = getattr(self, "db", None)
            if db is not None:
                await db.close()
                print(cf.cyan("[db] closed"))

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        await handleCommandError(ctx, error)

    async def on_error(self, event: str, *args, **kwargs) -> None:
        """Last resort: an exception raised by an event listener, not a command."""
        _, error, _ = sys.exc_info()
        if error is not None:
            logError(f"event {event}", error)

    async def syncCommands(self, guildId: int | None = None) -> int:
        """Register the slash commands with Discord. Returns how many landed.

        Scoped to the one guild, because that takes effect immediately — a
        global sync can take up to an hour to show up in clients. ``GUILD_ID``
        wins; failing that, the only server she's in.

        Prefix commands never need this: discord.py matches those locally, so
        they work the moment she starts. Slash commands don't exist until
        Discord has been told about them, which is what this does.
        """
        target = guildId or config.guildId
        if target is None:
            if len(self.guilds) != 1:
                print(
                    cf.red(
                        "[sync] set GUILD_ID in .env — I'm in"
                        f" {len(self.guilds)} servers and can't guess which"
                    )
                )
                return 0
            target = self.guilds[0].id
            print(
                cf.grey(
                    f"[sync] no GUILD_ID set; using the only server I'm in ({target})"
                )
            )

        guild = discord.Object(id=target)
        # The commands are declared globally, so copy them into this guild's
        # bucket before syncing it.
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
        except discord.Forbidden:
            print(
                cf.red(
                    "[sync] Discord refused — re-invite her with the"
                    " applications.commands scope"
                )
            )
            return 0
        except discord.HTTPException as e:
            print(cf.red(f"[sync] failed: {e}"))
            return 0

        print(cf.green(f"[sync] {len(synced)} slash command(s) live in {target}"))
        return len(synced)

    async def on_ready(self) -> None:
        # Guarded: on_ready fires again on every reconnect, and syncing is
        # rate-limited.
        if not self._synced:
            self._synced = True
            await self.syncCommands()

        if not self._profileSet:
            self._profileSet = True
            try:
                avatar_bytes = await asyncio.to_thread(
                    (config.assetsDir / "pfp.jpg").read_bytes
                )
                banner_bytes = await asyncio.to_thread(
                    (config.assetsDir / "banner.png").read_bytes
                )
                if self.user is not None:
                    await self.user.edit(avatar=avatar_bytes, banner=banner_bytes)
                print(cf.yellow("Avatar and Banner loaded!"))
            except (discord.HTTPException, OSError) as e:
                print(cf.red(f"Failed to set avatar/banner: {e}"))
        print(cf.magenta(f"Logged in as {self.user}"))


def createBot() -> Bot:
    intents = discord.Intents.default()
    # Privileged — tick "Message Content Intent" in the Developer Portal, or she
    # will only hear the @mention form of a prefix command.
    intents.message_content = True
    # Needed to resolve a message author to a Member (roles, rank checks).
    intents.members = True
    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name="I love Sandrone <3",
    )
    return Bot(
        command_prefix=resolvePrefix,
        owner_ids=set(config.owners),
        intents=intents,
        activity=activity,
        case_insensitive=True,
        strip_after_prefix=True,
        help_command=None,
    )


async def runBot() -> None:
    bot = createBot()

    async with bot:
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        def requestShutdown() -> None:
            print(cf.grey("\n[shutdown] signal received, closing bot..."))
            stop_event.set()

        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, requestShutdown)
        except NotImplementedError:

            def _handle(signum, frame):
                loop.call_soon_threadsafe(requestShutdown)

            signal.signal(signal.SIGINT, _handle)
            if hasattr(signal, "SIGBREAK"):
                signal.signal(signal.SIGBREAK, _handle)
            print(cf.blue("Windows machine detected, shutdown may not be graceful"))

        start_task = asyncio.create_task(bot.start(config.requireToken()))
        stop_task = asyncio.create_task(stop_event.wait())

        done, _ = await asyncio.wait(
            {start_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if stop_task in done:
            await bot.close()
            start_task.cancel()
        else:
            stop_task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await start_task

        print(cf.grey("[shutdown] bot closed"))


def main() -> None:
    if config.TOKEN is None:
        raise SystemExit("The Bot Token is not set, please configure .env")
    asyncio.run(runBot())

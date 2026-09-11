import os
from pathlib import Path
from typing import Any

import colorful as _colorful
import toml
from dotenvx import load_dotenv

load_dotenv()

# Terminal colour, used by every print in the project.
cf: Any = _colorful
cf.use_true_colors()

repoRoot = Path(__file__).resolve().parent.parent
commandsDir = repoRoot / "commands"
cogsDir = commandsDir / "cogs"
assetsDir = repoRoot / "assets"

version = "unknown"
pyproject_toml_file = repoRoot / "pyproject.toml"
if pyproject_toml_file.exists() and pyproject_toml_file.is_file():
    data = toml.load(pyproject_toml_file)
    if "project" in data and "version" in data["project"]:
        version = data["project"]["version"]

TOKEN = os.getenv("BOT_TOKEN")


def readId(raw: str | None) -> int | None:
    """A snowflake from the environment, or ``None`` if it isn't usable."""
    if raw is None or not raw.strip().isdigit():
        return None
    return int(raw.strip())


# The one server she lives in. Slash commands are synced here, which is instant
# — a global sync can take up to an hour to appear. Left unset, she falls back
# to the only server she's in.
guildId = readId(os.getenv("GUILD_ID"))


def resolveDir(raw: str | None, fallback: Path) -> Path:
    """A configured directory, read relative to the repo rather than the cwd.

    ``DATABASE_DIR=./data`` should mean the same thing no matter where she was
    started from, so a relative path is anchored to the repo root.
    """
    if not raw:
        return fallback
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (repoRoot / path).resolve()


databaseDir = resolveDir(os.getenv("DATABASE_DIR"), repoRoot / "data")
databasePath = databaseDir / "columbina.db"

prefixNames: list[str] = sorted(("damselette", "columbina", "bina"), key=len, reverse=True)

owners: list[int] = [
    1464890289922641993,
    1025770042245251122,
]


def requireToken() -> str:
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. Add it to your .env file before starting the bot."
        )
    return TOKEN

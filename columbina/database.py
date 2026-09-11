"""SQLite storage for moderation cases and honeypot channels.

One connection, opened at start-up and closed on shutdown. ``sqlite3`` is
blocking, so every query runs in a worker thread behind a lock — the event loop
never stalls on disk, and writes never interleave.

Each punishment gets a short random hash as its public case ID: ``a3f9c1d204``
is something a mod can quote in a channel or search for later, where a row
number would leak how many cases exist.
"""

import asyncio
import hashlib
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 2
HASH_LENGTH = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hash         TEXT    NOT NULL UNIQUE,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    action       TEXT    NOT NULL,
    reason       TEXT,
    created_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS cases_by_user ON cases (guild_id, user_id, action);

CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS honeypots (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    added_by   INTEGER NOT NULL,
    added_at   TEXT    NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
"""

# Every punishment kind she records. The value is the past-tense word used in
# replies, so adding a new action here is the only edit a new command needs.
ACTIONS: dict[str, str] = {
    "ban": "banned",
    "kick": "kicked",
    "warn": "warned",
}


def makeHash() -> str:
    """A short, unguessable case ID."""
    return hashlib.sha256(secrets.token_bytes(16)).hexdigest()[:HASH_LENGTH]


@dataclass(frozen=True, slots=True)
class Case:
    """One recorded punishment."""

    hash: str
    guildId: int
    userId: int
    moderatorId: int
    action: str
    reason: str | None
    createdAt: datetime

    @classmethod
    def fromRow(cls, row: sqlite3.Row) -> "Case":
        return cls(
            hash=row["hash"],
            guildId=row["guild_id"],
            userId=row["user_id"],
            moderatorId=row["moderator_id"],
            action=row["action"],
            reason=row["reason"],
            createdAt=datetime.fromisoformat(row["created_at"]),
        )


class Database:
    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, path: Path) -> "Database":
        """Open (creating if needed) the database at ``path``."""

        def open() -> sqlite3.Connection:
            path.parent.mkdir(parents=True, exist_ok=True)
            # isolation_level=None: autocommit, so each statement lands as soon
            # as it runs and a crash can't swallow a ban we already announced.
            connection = sqlite3.connect(
                path, check_same_thread=False, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            return connection

        return cls(path, await asyncio.to_thread(open))

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)

    async def run[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``work`` against the connection in a worker thread."""
        async with self._lock:
            return await asyncio.to_thread(work, self._connection)

    async def addCase(
        self,
        *,
        guildId: int,
        userId: int,
        moderatorId: int,
        action: str,
        reason: str | None,
    ) -> Case:
        """Record a punishment and hand back the stored case."""
        created = datetime.now(UTC)

        def insert(connection: sqlite3.Connection) -> str:
            # A 10-hex-char collision is vanishingly unlikely, but the UNIQUE
            # index is the thing that decides, so just take the next one.
            for _ in range(5):
                candidate = makeHash()
                try:
                    connection.execute(
                        "INSERT INTO cases"
                        " (hash, guild_id, user_id, moderator_id, action, reason,"
                        "  created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            candidate,
                            guildId,
                            userId,
                            moderatorId,
                            action,
                            reason,
                            created.isoformat(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue
                return candidate
            raise RuntimeError("could not find a free case hash")

        return Case(
            hash=await self.run(insert),
            guildId=guildId,
            userId=userId,
            moderatorId=moderatorId,
            action=action,
            reason=reason,
            createdAt=created,
        )

    async def case(self, hash: str) -> Case | None:
        def select(connection: sqlite3.Connection) -> sqlite3.Row | None:
            return connection.execute(
                "SELECT * FROM cases WHERE hash = ?", (hash,)
            ).fetchone()

        row = await self.run(select)
        return Case.fromRow(row) if row is not None else None

    async def casesFor(
        self, guildId: int, userId: int, *, action: str | None = None, limit: int = 25
    ) -> list[Case]:
        """This user's cases, newest first."""

        def select(connection: sqlite3.Connection) -> list[sqlite3.Row]:
            sql = "SELECT * FROM cases WHERE guild_id = ? AND user_id = ?"
            params: list[object] = [guildId, userId]
            if action is not None:
                sql += " AND action = ?"
                params.append(action)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            return connection.execute(sql, params).fetchall()

        return [Case.fromRow(row) for row in await self.run(select)]

    async def countCases(self, guildId: int, userId: int, action: str) -> int:
        def count(connection: sqlite3.Connection) -> int:
            row = connection.execute(
                "SELECT COUNT(*) FROM cases"
                " WHERE guild_id = ? AND user_id = ? AND action = ?",
                (guildId, userId, action),
            ).fetchone()
            return int(row[0])

        return await self.run(count)

    async def setting(self, guildId: int, key: str) -> str | None:
        def select(connection: sqlite3.Connection) -> sqlite3.Row | None:
            return connection.execute(
                "SELECT value FROM settings WHERE guild_id = ? AND key = ?",
                (guildId, key),
            ).fetchone()

        row = await self.run(select)
        return row["value"] if row is not None else None

    async def setSetting(self, guildId: int, key: str, value: str) -> None:
        def upsert(connection: sqlite3.Connection) -> None:
            connection.execute(
                "INSERT INTO settings (guild_id, key, value) VALUES (?, ?, ?)"
                " ON CONFLICT (guild_id, key) DO UPDATE SET value = excluded.value",
                (guildId, key, value),
            )

        await self.run(upsert)

    async def clearSetting(self, guildId: int, key: str) -> bool:
        """``False`` if there was nothing set."""

        def delete(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "DELETE FROM settings WHERE guild_id = ? AND key = ?", (guildId, key)
            )
            return cursor.rowcount > 0

        return await self.run(delete)

    async def addHoneypot(self, guildId: int, channelId: int, addedBy: int) -> bool:
        """Mark a channel as a trap. ``False`` if it already was one."""

        def insert(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO honeypots"
                " (guild_id, channel_id, added_by, added_at) VALUES (?, ?, ?, ?)",
                (guildId, channelId, addedBy, datetime.now(UTC).isoformat()),
            )
            return cursor.rowcount > 0

        return await self.run(insert)

    async def removeHoneypot(self, guildId: int, channelId: int) -> bool:
        """Stop trapping a channel. ``False`` if it wasn't one."""

        def delete(connection: sqlite3.Connection) -> bool:
            cursor = connection.execute(
                "DELETE FROM honeypots WHERE guild_id = ? AND channel_id = ?",
                (guildId, channelId),
            )
            return cursor.rowcount > 0

        return await self.run(delete)

    async def honeypots(self, guildId: int) -> list[int]:
        def select(connection: sqlite3.Connection) -> list[sqlite3.Row]:
            return connection.execute(
                "SELECT channel_id FROM honeypots WHERE guild_id = ? ORDER BY added_at",
                (guildId,),
            ).fetchall()

        return [row["channel_id"] for row in await self.run(select)]

    async def allHoneypots(self) -> set[int]:
        """Every trapped channel, for the listener's in-memory cache."""

        def select(connection: sqlite3.Connection) -> list[sqlite3.Row]:
            return connection.execute("SELECT channel_id FROM honeypots").fetchall()

        return {row["channel_id"] for row in await self.run(select)}

"""``.jp`` storage for moderation cases and honeypot channels.

The whole store is one :mod:`jpml` document, held in memory and written back
after every change. Top-level sections are guild IDs, which is the shape ``.jp``
was built for::

    [1234567890]
    settings: {
      log_channel: "999"
    }
    honeypots: [...]
    cases: [...]

Writing is blocking, so every save runs in a worker thread behind a lock — the
event loop never stalls on disk, and two commands can't interleave a
read-modify-write. :func:`jpml.dump` writes through a temp file and ``fsync``,
so a crash leaves either the old file or the new one, never half of one.

Each punishment gets a short random hash as its public case ID: ``a3f9c1d204``
is something a mod can quote in a channel or search for later, where a row
number would leak how many cases exist.
"""

import asyncio
import copy
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jpml

SCHEMA_VERSION = 3
HASH_LENGTH = 10

# Guild IDs are digits, so a word can never collide with one — the meta section
# is safe to keep in the same document.
META_SECTION = "columbina"

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
    def fromEntry(cls, guildId: int, entry: dict[str, Any]) -> "Case":
        return cls(
            hash=entry["hash"],
            guildId=guildId,
            userId=entry["user_id"],
            moderatorId=entry["moderator_id"],
            action=entry["action"],
            reason=entry["reason"],
            createdAt=datetime.fromisoformat(entry["created_at"]),
        )

    def toEntry(self) -> dict[str, Any]:
        """The stored form — no guild ID, since the section it sits in is one."""
        return {
            "hash": self.hash,
            "user_id": self.userId,
            "moderator_id": self.moderatorId,
            "action": self.action,
            "reason": self.reason,
            "created_at": self.createdAt.isoformat(),
        }


class Database:
    def __init__(self, path: Path, document: dict[str, Any]) -> None:
        self.path = path
        self._document = document
        self._lock = asyncio.Lock()
        # Case hashes are never reused or deleted, so an index built once at
        # load stays true with nothing but an insert to keep it current. It
        # makes case() a lookup instead of a walk over every guild.
        self._byHash: dict[str, Case] = {
            case.hash: case
            for guildId, section in self._guilds()
            for case in (
                Case.fromEntry(guildId, entry) for entry in section.get("cases", ())
            )
        }

    @classmethod
    async def connect(cls, path: Path) -> "Database":
        """Open (creating if needed) the store at ``path``."""

        def open() -> dict[str, Any]:
            document: dict[str, Any] = jpml.load(path) if path.exists() else {}
            document.setdefault(META_SECTION, {})["version"] = SCHEMA_VERSION
            # Write straight back, so a fresh install has a real file on disk
            # rather than one that appears at the first ban.
            jpml.dump(document, path)
            return document

        return cls(path, await asyncio.to_thread(open))

    async def close(self) -> None:
        """Flush once more on the way out."""
        async with self._lock:
            await self._save()

    def _guilds(self):
        """Every guild section, meta skipped."""
        for name, section in self._document.items():
            if name != META_SECTION:
                yield int(name), section

    def _section(self, guildId: int, *, create: bool = False) -> dict[str, Any]:
        """One guild's section — empty and unattached unless ``create``."""
        key = str(guildId)
        if create:
            return self._document.setdefault(key, {})
        return self._document.get(key, {})

    async def _save(self) -> None:
        """Persist the document. Caller holds the lock."""
        # A snapshot, because jpml walks the document in the worker thread and
        # the event loop is free to mutate the real one while it does.
        snapshot = copy.deepcopy(self._document)
        await asyncio.to_thread(jpml.dump, snapshot, self.path)

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
        async with self._lock:
            # A 10-hex-char collision is vanishingly unlikely, but the index is
            # the thing that decides, so just take the next one.
            for _ in range(5):
                candidate = makeHash()
                if candidate not in self._byHash:
                    break
            else:
                raise RuntimeError("could not find a free case hash")

            case = Case(
                hash=candidate,
                guildId=guildId,
                userId=userId,
                moderatorId=moderatorId,
                action=action,
                reason=reason,
                createdAt=datetime.now(UTC),
            )
            self._section(guildId, create=True).setdefault("cases", []).append(
                case.toEntry()
            )
            self._byHash[case.hash] = case
            await self._save()
            return case

    async def case(self, hash: str) -> Case | None:
        return self._byHash.get(hash)

    async def casesFor(
        self, guildId: int, userId: int, *, action: str | None = None, limit: int = 25
    ) -> list[Case]:
        """This user's cases, newest first."""
        entries = self._section(guildId).get("cases", ())
        found: list[Case] = []
        # Reversed, because cases are appended in the order they happened.
        for entry in reversed(entries):
            if entry["user_id"] != userId:
                continue
            if action is not None and entry["action"] != action:
                continue
            found.append(Case.fromEntry(guildId, entry))
            if len(found) >= limit:
                break
        return found

    async def countCases(self, guildId: int, userId: int, action: str) -> int:
        return sum(
            1
            for entry in self._section(guildId).get("cases", ())
            if entry["user_id"] == userId and entry["action"] == action
        )

    async def setting(self, guildId: int, key: str) -> str | None:
        return self._section(guildId).get("settings", {}).get(key)

    async def setSetting(self, guildId: int, key: str, value: str) -> None:
        async with self._lock:
            self._section(guildId, create=True).setdefault("settings", {})[key] = value
            await self._save()

    async def clearSetting(self, guildId: int, key: str) -> bool:
        """``False`` if there was nothing set."""
        async with self._lock:
            settings = self._section(guildId).get("settings", {})
            if key not in settings:
                return False
            del settings[key]
            await self._save()
            return True

    def _honeypot(self, guildId: int, channelId: int) -> dict[str, Any] | None:
        for entry in self._section(guildId).get("honeypots", ()):
            if entry["channel_id"] == channelId:
                return entry
        return None

    async def addHoneypot(self, guildId: int, channelId: int, addedBy: int) -> bool:
        """Mark a channel as a trap. ``False`` if it already was one."""
        async with self._lock:
            if self._honeypot(guildId, channelId) is not None:
                return False
            self._section(guildId, create=True).setdefault("honeypots", []).append(
                {
                    "channel_id": channelId,
                    "added_by": addedBy,
                    "added_at": datetime.now(UTC).isoformat(),
                }
            )
            await self._save()
            return True

    async def removeHoneypot(self, guildId: int, channelId: int) -> bool:
        """Stop trapping a channel. ``False`` if it wasn't one."""
        async with self._lock:
            entry = self._honeypot(guildId, channelId)
            if entry is None:
                return False
            self._section(guildId)["honeypots"].remove(entry)
            await self._save()
            return True

    async def honeypots(self, guildId: int) -> list[int]:
        # Appended as they were added, so file order is already added_at order.
        return [
            entry["channel_id"] for entry in self._section(guildId).get("honeypots", ())
        ]

    async def allHoneypots(self) -> set[int]:
        """Every trapped channel, for the listener's in-memory cache."""
        return {
            entry["channel_id"]
            for _, section in self._guilds()
            for entry in section.get("honeypots", ())
        }

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PunishmentClaim:
    group_id: str
    message_id: str
    target_user_id: str
    duration_seconds: int
    api_attempts: int


@dataclass(frozen=True, slots=True)
class MessageState:
    group_id: str
    message_id: str
    sender_id: str
    status: str
    intended_duration: int | None
    api_attempts: int
    last_error: str | None
    punished_at: float | None


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                group_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'collecting'
                    CHECK (status IN (
                        'collecting', 'mute_pending', 'mute_failed',
                        'mute_exhausted', 'punished', 'ineligible'
                    )),
                intended_duration INTEGER,
                api_attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                punished_at REAL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (group_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS votes (
                group_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                voter_id TEXT NOT NULL,
                reaction_id TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                updated_at REAL NOT NULL,
                PRIMARY KEY (group_id, message_id, voter_id, reaction_id),
                FOREIGN KEY (group_id, message_id)
                    REFERENCES messages(group_id, message_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS punishments (
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                last_punished_at REAL NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_votes_active
                ON votes(group_id, message_id, reaction_id, active);
            """
        )

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()

    async def healthcheck(self) -> bool:
        async with self._lock:
            row = self._conn.execute("SELECT 1 AS ok").fetchone()
        return bool(row and int(row["ok"]) == 1)

    async def get_message(self, group_id: str, message_id: str) -> MessageState | None:
        async with self._lock:
            row = self._conn.execute(
                "SELECT * FROM messages WHERE group_id=? AND message_id=?",
                (group_id, message_id),
            ).fetchone()
        return self._row_to_state(row) if row else None

    async def ensure_message(
        self, group_id: str, message_id: str, sender_id: str, now: float | None = None
    ) -> MessageState:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO messages(group_id, message_id, sender_id, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(group_id, message_id) DO NOTHING
                    """,
                    (group_id, message_id, sender_id, now),
                )
                row = self._conn.execute(
                    "SELECT * FROM messages WHERE group_id=? AND message_id=?",
                    (group_id, message_id),
                ).fetchone()
                if row is None:
                    raise RuntimeError("message row disappeared during ensure_message")
                if row["sender_id"] != sender_id:
                    raise ValueError("stored sender does not match resolved message sender")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self._row_to_state(row)

    async def apply_vote(
        self,
        group_id: str,
        message_id: str,
        voter_id: str,
        reaction_id: str,
        is_add: bool,
        now: float | None = None,
    ) -> int:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO votes(
                        group_id, message_id, voter_id, reaction_id, active, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(group_id, message_id, voter_id, reaction_id)
                    DO UPDATE SET active=excluded.active, updated_at=excluded.updated_at
                    """,
                    (group_id, message_id, voter_id, reaction_id, int(is_add), now),
                )
                count = self._active_vote_count(group_id, message_id, reaction_id)
                self._conn.execute(
                    "UPDATE messages SET updated_at=? WHERE group_id=? AND message_id=?",
                    (now, group_id, message_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return count

    async def reconcile_votes(
        self,
        group_id: str,
        message_id: str,
        reaction_id: str,
        voter_ids: set[str],
        now: float | None = None,
    ) -> int:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE votes SET active=0, updated_at=?
                    WHERE group_id=? AND message_id=? AND reaction_id=?
                    """,
                    (now, group_id, message_id, reaction_id),
                )
                for voter_id in voter_ids:
                    self._conn.execute(
                        """
                        INSERT INTO votes(
                            group_id, message_id, voter_id, reaction_id, active, updated_at
                        ) VALUES (?, ?, ?, ?, 1, ?)
                        ON CONFLICT(group_id, message_id, voter_id, reaction_id)
                        DO UPDATE SET active=1, updated_at=excluded.updated_at
                        """,
                        (group_id, message_id, voter_id, reaction_id, now),
                    )
                count = self._active_vote_count(group_id, message_id, reaction_id)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return count

    def _active_vote_count(self, group_id: str, message_id: str, reaction_id: str) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(DISTINCT voter_id) AS n
            FROM votes
            WHERE group_id=? AND message_id=? AND reaction_id=? AND active=1
            """,
            (group_id, message_id, reaction_id),
        ).fetchone()
        return int(row["n"])

    async def claim_punishment(
        self,
        group_id: str,
        message_id: str,
        reaction_id: str,
        threshold: int,
        first_duration: int,
        repeat_duration: int,
        repeat_window_seconds: int,
        now: float | None = None,
    ) -> PunishmentClaim | None:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM messages WHERE group_id=? AND message_id=?",
                    (group_id, message_id),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                if row["status"] not in {"collecting", "mute_failed"}:
                    self._conn.execute("COMMIT")
                    return None
                if row["api_attempts"] >= 3:
                    self._conn.execute(
                        """
                        UPDATE messages SET status='mute_exhausted', updated_at=?
                        WHERE group_id=? AND message_id=?
                        """,
                        (now, group_id, message_id),
                    )
                    self._conn.execute("COMMIT")
                    return None
                count = self._active_vote_count(group_id, message_id, reaction_id)
                if count < threshold:
                    self._conn.execute("COMMIT")
                    return None

                duration = row["intended_duration"]
                if duration is None:
                    prior = self._conn.execute(
                        """
                        SELECT last_punished_at FROM punishments
                        WHERE group_id=? AND user_id=?
                        """,
                        (group_id, row["sender_id"]),
                    ).fetchone()
                    if prior and now - float(prior["last_punished_at"]) <= repeat_window_seconds:
                        duration = repeat_duration
                    else:
                        duration = first_duration

                updated = self._conn.execute(
                    """
                    UPDATE messages
                    SET status='mute_pending', intended_duration=?, last_error=NULL, updated_at=?
                    WHERE group_id=? AND message_id=?
                      AND status IN ('collecting', 'mute_failed')
                    """,
                    (duration, now, group_id, message_id),
                ).rowcount
                self._conn.execute("COMMIT")
                if updated != 1:
                    return None
                return PunishmentClaim(
                    group_id=group_id,
                    message_id=message_id,
                    target_user_id=str(row["sender_id"]),
                    duration_seconds=int(duration),
                    api_attempts=int(row["api_attempts"]),
                )
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    async def record_api_attempt(
        self, group_id: str, message_id: str, now: float | None = None
    ) -> int:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    UPDATE messages
                    SET api_attempts=api_attempts+1, updated_at=?
                    WHERE group_id=? AND message_id=? AND status='mute_pending'
                    """,
                    (now, group_id, message_id),
                )
                row = self._conn.execute(
                    "SELECT api_attempts FROM messages WHERE group_id=? AND message_id=?",
                    (group_id, message_id),
                ).fetchone()
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        if row is None:
            raise RuntimeError("message disappeared while recording API attempt")
        return int(row["api_attempts"])

    async def mark_failed(
        self,
        group_id: str,
        message_id: str,
        error: str,
        *,
        exhausted: bool = False,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        status = "mute_exhausted" if exhausted else "mute_failed"
        async with self._lock:
            self._conn.execute(
                """
                UPDATE messages SET status=?, last_error=?, updated_at=?
                WHERE group_id=? AND message_id=? AND status='mute_pending'
                """,
                (status, error[:500], now, group_id, message_id),
            )

    async def mark_ineligible(
        self, group_id: str, message_id: str, reason: str, now: float | None = None
    ) -> None:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute(
                """
                UPDATE messages SET status='ineligible', last_error=?, updated_at=?
                WHERE group_id=? AND message_id=? AND status='mute_pending'
                """,
                (reason[:500], now, group_id, message_id),
            )

    async def mark_success(
        self,
        group_id: str,
        message_id: str,
        target_user_id: str,
        duration_seconds: int,
        now: float | None = None,
    ) -> None:
        now = time.time() if now is None else now
        async with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                updated = self._conn.execute(
                    """
                    UPDATE messages
                    SET status='punished', intended_duration=?, punished_at=?,
                        last_error=NULL, updated_at=?
                    WHERE group_id=? AND message_id=? AND status='mute_pending'
                    """,
                    (duration_seconds, now, now, group_id, message_id),
                ).rowcount
                if updated == 1:
                    self._conn.execute(
                        """
                        INSERT INTO punishments(group_id, user_id, last_punished_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(group_id, user_id)
                        DO UPDATE SET last_punished_at=excluded.last_punished_at
                        """,
                        (group_id, target_user_id, now),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    async def set_last_punished_at(self, group_id: str, user_id: str, when: float) -> None:
        """Test/support helper for controlled policy state."""
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO punishments(group_id, user_id, last_punished_at)
                VALUES (?, ?, ?)
                ON CONFLICT(group_id, user_id)
                DO UPDATE SET last_punished_at=excluded.last_punished_at
                """,
                (group_id, user_id, when),
            )

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> MessageState:
        return MessageState(
            group_id=str(row["group_id"]),
            message_id=str(row["message_id"]),
            sender_id=str(row["sender_id"]),
            status=str(row["status"]),
            intended_duration=(
                int(row["intended_duration"]) if row["intended_duration"] is not None else None
            ),
            api_attempts=int(row["api_attempts"]),
            last_error=str(row["last_error"]) if row["last_error"] is not None else None,
            punished_at=float(row["punished_at"]) if row["punished_at"] is not None else None,
        )

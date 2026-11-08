"""SQLite storage (aiosqlite). All money is stored as integers."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id             INTEGER PRIMARY KEY,
    review_channel_id    INTEGER,
    report_channel_id    INTEGER,
    last_report_boundary INTEGER NOT NULL DEFAULT 0,
    audit_channel_id     INTEGER,
    last_midweek_boundary INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reviewer_roles (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS targets (
    channel_id    INTEGER PRIMARY KEY,
    guild_id      INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    default_price INTEGER NOT NULL,
    prices        TEXT    NOT NULL DEFAULT '[]',
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_targets_guild ON targets (guild_id);

CREATE TABLE IF NOT EXISTS submissions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    target_channel_id INTEGER NOT NULL,
    target_name       TEXT    NOT NULL,
    source_channel_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL UNIQUE,
    submitter_id      INTEGER NOT NULL,
    jump_url          TEXT    NOT NULL DEFAULT '',
    price             INTEGER NOT NULL,
    price_source      TEXT    NOT NULL,
    shots             TEXT    NOT NULL DEFAULT '[]',
    status            TEXT    NOT NULL DEFAULT 'pending',
    reject_reason     TEXT,
    decided_by        INTEGER,
    decided_at        INTEGER,
    review_channel_id INTEGER,
    review_message_id INTEGER,
    created_at        INTEGER NOT NULL,
    deleted_by        INTEGER,
    deleted_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_sub_status ON submissions (guild_id, status);

CREATE TABLE IF NOT EXISTS submission_entries (
    submission_id INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    kills         INTEGER NOT NULL,
    PRIMARY KEY (submission_id, user_id)
);

CREATE TABLE IF NOT EXISTS kill_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    submission_id     INTEGER NOT NULL,
    target_channel_id INTEGER NOT NULL,
    target_name       TEXT    NOT NULL,
    user_id           INTEGER NOT NULL,
    kills             INTEGER NOT NULL,
    price             INTEGER NOT NULL,
    amount            INTEGER NOT NULL,
    occurred_at       INTEGER NOT NULL,
    approved_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_time ON kill_logs (guild_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_logs_user ON kill_logs (guild_id, user_id, occurred_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    action     TEXT    NOT NULL,
    actor_id   INTEGER NOT NULL,
    target_id  INTEGER,
    detail     TEXT    NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_guild ON audit_log (guild_id, created_at DESC);

CREATE TABLE IF NOT EXISTS claim_rate (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    claimed_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claim_rate ON claim_rate (guild_id, user_id, claimed_at);
"""


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()  # serialises write transactions

    # ------------------------------------------------------------------ setup
    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        # migrate older DBs that lack new columns
        await self._migrate()
        if self.path != ":memory:":
            await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.commit()

    async def _migrate(self) -> None:
        assert self.conn is not None
        cols = {r[1] for r in await (await self.conn.execute("PRAGMA table_info(guild_settings)")).fetchall()}
        if "audit_channel_id" not in cols:
            await self.conn.execute("ALTER TABLE guild_settings ADD COLUMN audit_channel_id INTEGER")
        if "last_midweek_boundary" not in cols:
            await self.conn.execute(
                "ALTER TABLE guild_settings ADD COLUMN last_midweek_boundary INTEGER NOT NULL DEFAULT 0"
            )
        sub_cols = {r[1] for r in await (await self.conn.execute("PRAGMA table_info(submissions)")).fetchall()}
        if "deleted_by" not in sub_cols:
            await self.conn.execute("ALTER TABLE submissions ADD COLUMN deleted_by INTEGER")
        if "deleted_at" not in sub_cols:
            await self.conn.execute("ALTER TABLE submissions ADD COLUMN deleted_at INTEGER")

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        assert self.conn is not None
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        assert self.conn is not None
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        await cur.close()
        return _row(row)

    # --------------------------------------------------------- guild settings
    async def get_settings(self, guild_id: int) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,))

    async def set_review_channel(self, guild_id: int, channel_id: int, boundary: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO guild_settings (guild_id, review_channel_id, last_report_boundary) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET review_channel_id=excluded.review_channel_id",
                (guild_id, channel_id, boundary),
            )
            await self.conn.commit()

    async def set_report_channel(self, guild_id: int, channel_id: int | None, boundary: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO guild_settings (guild_id, report_channel_id, last_report_boundary) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET report_channel_id=excluded.report_channel_id",
                (guild_id, channel_id, boundary),
            )
            await self.conn.commit()

    async def set_audit_channel(self, guild_id: int, channel_id: int | None) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO guild_settings (guild_id, audit_channel_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET audit_channel_id=excluded.audit_channel_id",
                (guild_id, channel_id),
            )
            await self.conn.commit()

    async def set_last_report_boundary(self, guild_id: int, boundary: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "UPDATE guild_settings SET last_report_boundary=? WHERE guild_id=?",
                (boundary, guild_id),
            )
            await self.conn.commit()

    async def set_last_midweek_boundary(self, guild_id: int, boundary: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "UPDATE guild_settings SET last_midweek_boundary=? WHERE guild_id=?",
                (boundary, guild_id),
            )
            await self.conn.commit()

    async def list_configured_guilds(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM guild_settings WHERE review_channel_id IS NOT NULL OR report_channel_id IS NOT NULL"
        )

    # ---------------------------------------------------------- reviewer roles
    async def add_reviewer_role(self, guild_id: int, role_id: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO reviewer_roles (guild_id, role_id) VALUES (?, ?)",
                (guild_id, role_id),
            )
            await self.conn.commit()

    async def remove_reviewer_role(self, guild_id: int, role_id: int) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "DELETE FROM reviewer_roles WHERE guild_id=? AND role_id=?", (guild_id, role_id)
            )
            await self.conn.commit()

    async def reviewer_role_ids(self, guild_id: int) -> set[int]:
        rows = await self._all("SELECT role_id FROM reviewer_roles WHERE guild_id=?", (guild_id,))
        return {r["role_id"] for r in rows}

    # ----------------------------------------------------------------- targets
    async def upsert_target(
        self, channel_id: int, guild_id: int, name: str, default_price: int, prices: list[int]
    ) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO targets (channel_id, guild_id, name, default_price, prices, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name, "
                "default_price=excluded.default_price, prices=excluded.prices",
                (channel_id, guild_id, name, default_price, json.dumps(prices), int(time.time())),
            )
            await self.conn.commit()

    async def get_target(self, channel_id: int) -> dict[str, Any] | None:
        row = await self._one("SELECT * FROM targets WHERE channel_id=?", (channel_id,))
        if row:
            row["prices"] = json.loads(row["prices"])
        return row

    async def delete_target(self, channel_id: int) -> bool:
        assert self.conn is not None
        async with self._lock:
            cur = await self.conn.execute("DELETE FROM targets WHERE channel_id=?", (channel_id,))
            await self.conn.commit()
            deleted = cur.rowcount > 0
            await cur.close()
            return deleted

    async def list_targets(self, guild_id: int) -> list[dict[str, Any]]:
        rows = await self._all("SELECT * FROM targets WHERE guild_id=? ORDER BY name", (guild_id,))
        for r in rows:
            r["prices"] = json.loads(r["prices"])
        return rows

    async def target_name_exists(self, guild_id: int, name: str) -> bool:
        row = await self._one(
            "SELECT 1 AS x FROM targets WHERE guild_id=? AND LOWER(name)=LOWER(?) LIMIT 1", (guild_id, name)
        )
        return row is not None

    # ------------------------------------------------------------- submissions
    async def create_submission(
        self,
        *,
        guild_id: int,
        target_channel_id: int,
        target_name: str,
        source_channel_id: int,
        source_message_id: int,
        submitter_id: int,
        jump_url: str,
        price: int,
        price_source: str,
        entries: dict[int, int],
        created_at: int,
    ) -> int | None:
        """Returns the new submission id, or None if this message was already recorded."""
        assert self.conn is not None
        async with self._lock:
            cur = await self.conn.execute(
                "INSERT OR IGNORE INTO submissions (guild_id, target_channel_id, target_name, "
                "source_channel_id, source_message_id, submitter_id, jump_url, price, price_source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, target_channel_id, target_name, source_channel_id, source_message_id,
                 submitter_id, jump_url, price, price_source, created_at),
            )
            if cur.rowcount == 0:
                await cur.close()
                await self.conn.rollback()
                return None
            sid = cur.lastrowid
            await cur.close()
            await self.conn.executemany(
                "INSERT INTO submission_entries (submission_id, user_id, kills) VALUES (?, ?, ?)",
                [(sid, uid, kills) for uid, kills in entries.items()],
            )
            await self.conn.commit()
            return int(sid)

    async def get_submission(self, sid: int) -> dict[str, Any] | None:
        row = await self._one("SELECT * FROM submissions WHERE id=?", (sid,))
        if row:
            row["shots"] = json.loads(row["shots"])
        return row

    async def delete_submission(self, sid: int) -> None:
        """Used only when the review message could not be posted."""
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute("DELETE FROM submission_entries WHERE submission_id=?", (sid,))
            await self.conn.execute("DELETE FROM submissions WHERE id=? AND status='pending'", (sid,))
            await self.conn.commit()

    async def get_entries(self, sid: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT user_id, kills FROM submission_entries WHERE submission_id=? ORDER BY rowid", (sid,)
        )

    async def set_review_message(self, sid: int, channel_id: int, message_id: int, shots: list[str]) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "UPDATE submissions SET review_channel_id=?, review_message_id=?, shots=? WHERE id=?",
                (channel_id, message_id, json.dumps(shots), sid),
            )
            await self.conn.commit()

    async def set_entry(self, sid: int, user_id: int, kills: int, *, allow_accepted: bool = False) -> bool:
        """Add / change a member's kills. kills <= 0 removes the member.

        Works while the claim is pending. With ``allow_accepted=True`` it also works on an accepted
        claim and keeps ``kill_logs`` (what the reports/Excel read) in sync. If the last member of an
        accepted claim is removed, the claim is deleted (nothing left to count).
        """
        assert self.conn is not None
        async with self._lock:
            sub = await self._one("SELECT * FROM submissions WHERE id=?", (sid,))
            if not sub:
                return False
            allowed = ("pending", "accepted") if allow_accepted else ("pending",)
            if sub["status"] not in allowed:
                return False
            accepted = sub["status"] == "accepted"
            if kills <= 0:
                await self.conn.execute(
                    "DELETE FROM submission_entries WHERE submission_id=? AND user_id=?", (sid, user_id)
                )
                if accepted:
                    await self.conn.execute(
                        "DELETE FROM kill_logs WHERE submission_id=? AND user_id=?", (sid, user_id)
                    )
                    left = await self._one(
                        "SELECT COUNT(*) AS n FROM submission_entries WHERE submission_id=?", (sid,)
                    )
                    if left and int(left["n"]) == 0:
                        await self.conn.execute(
                            "UPDATE submissions SET status='deleted', deleted_at=? WHERE id=?",
                            (int(time.time()), sid),
                        )
            else:
                await self.conn.execute(
                    "INSERT INTO submission_entries (submission_id, user_id, kills) VALUES (?, ?, ?) "
                    "ON CONFLICT(submission_id, user_id) DO UPDATE SET kills=excluded.kills",
                    (sid, user_id, kills),
                )
                if accepted:
                    price = int(sub["price"])
                    cur = await self.conn.execute(
                        "UPDATE kill_logs SET kills=?, amount=? WHERE submission_id=? AND user_id=?",
                        (kills, kills * price, sid, user_id),
                    )
                    updated = cur.rowcount
                    await cur.close()
                    if updated == 0:
                        await self.conn.execute(
                            "INSERT INTO kill_logs (guild_id, submission_id, target_channel_id, target_name, "
                            "user_id, kills, price, amount, occurred_at, approved_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (sub["guild_id"], sid, sub["target_channel_id"], sub["target_name"], user_id,
                             kills, price, kills * price, sub["created_at"], sub["decided_at"] or int(time.time())),
                        )
            await self.conn.commit()
            return True

    async def set_price(self, sid: int, price: int, *, allow_accepted: bool = False) -> bool:
        """Change the price per kill. With ``allow_accepted=True`` also re-prices an accepted claim."""
        assert self.conn is not None
        async with self._lock:
            sub = await self._one("SELECT status FROM submissions WHERE id=?", (sid,))
            if not sub:
                return False
            allowed = ("pending", "accepted") if allow_accepted else ("pending",)
            if sub["status"] not in allowed:
                return False
            await self.conn.execute(
                "UPDATE submissions SET price=?, price_source='edited' WHERE id=?", (price, sid)
            )
            if sub["status"] == "accepted":
                await self.conn.execute(
                    "UPDATE kill_logs SET price=?, amount=kills*? WHERE submission_id=?", (price, price, sid)
                )
            await self.conn.commit()
            return True

    async def delete_claim(self, sid: int, deleted_by: int) -> bool:
        """Remove a pending or accepted claim from the count for good.

        The claim row is kept (status 'deleted') so the same Discord message can never be
        re-submitted, but every kill_logs row is erased, so reports / Excel no longer count it.
        """
        assert self.conn is not None
        async with self._lock:
            sub = await self._one("SELECT status FROM submissions WHERE id=?", (sid,))
            if not sub or sub["status"] not in ("pending", "accepted"):
                return False
            await self.conn.execute("DELETE FROM kill_logs WHERE submission_id=?", (sid,))
            await self.conn.execute(
                "UPDATE submissions SET status='deleted', deleted_by=?, deleted_at=? WHERE id=?",
                (deleted_by, int(time.time()), sid),
            )
            await self.conn.commit()
            return True

    async def accept(self, sid: int, decided_by: int) -> bool:
        """Mark accepted and write one kill_log row per member. False if already decided
        or if there is nothing to log."""
        assert self.conn is not None
        now = int(time.time())
        async with self._lock:
            sub = await self._one("SELECT * FROM submissions WHERE id=?", (sid,))
            if not sub or sub["status"] != "pending":
                return False
            entries = await self._all(
                "SELECT user_id, kills FROM submission_entries WHERE submission_id=?", (sid,)
            )
            if not entries:
                return False
            await self.conn.execute(
                "UPDATE submissions SET status='accepted', decided_by=?, decided_at=? WHERE id=?",
                (decided_by, now, sid),
            )
            await self.conn.executemany(
                "INSERT INTO kill_logs (guild_id, submission_id, target_channel_id, target_name, user_id, "
                "kills, price, amount, occurred_at, approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (sub["guild_id"], sid, sub["target_channel_id"], sub["target_name"], e["user_id"],
                     e["kills"], sub["price"], e["kills"] * sub["price"], sub["created_at"], now)
                    for e in entries
                ],
            )
            await self.conn.commit()
            return True

    async def reject(self, sid: int, decided_by: int, reason: str) -> bool:
        assert self.conn is not None
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE submissions SET status='rejected', reject_reason=?, decided_by=?, decided_at=? "
                "WHERE id=? AND status='pending'",
                (reason, decided_by, int(time.time()), sid),
            )
            await self.conn.commit()
            ok = cur.rowcount > 0
            await cur.close()
            return ok

    async def pending_count(self, guild_id: int, start_ts: int, end_ts: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM submissions WHERE guild_id=? AND status='pending' "
            "AND created_at>=? AND created_at<?",
            (guild_id, start_ts, end_ts),
        )
        return int(row["n"]) if row else 0

    async def list_pending(self, guild_id: int, limit: int = 25) -> list[dict[str, Any]]:
        """Pending claims newest first, with entry summary."""
        rows = await self._all(
            "SELECT * FROM submissions WHERE guild_id=? AND status='pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        )
        for r in rows:
            r["shots"] = json.loads(r["shots"])
            entries = await self.get_entries(r["id"])
            r["entries"] = entries
            r["total_kills"] = sum(int(e["kills"]) for e in entries)
        return rows

    # ----------------------------------------------------------------- reports
    async def totals(
        self,
        guild_id: int,
        start_ts: int,
        end_ts: int,
        *,
        target_name: str | None = None,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Rows of (target_name, user_id, kills, amount) for accepted kills in [start, end)."""
        sql = (
            "SELECT target_name, user_id, SUM(kills) AS kills, SUM(amount) AS amount FROM kill_logs "
            "WHERE guild_id=? AND occurred_at>=? AND occurred_at<?"
        )
        params: list[Any] = [guild_id, start_ts, end_ts]
        if target_name is not None:
            sql += " AND LOWER(target_name)=LOWER(?)"
            params.append(target_name)
        if user_id is not None:
            sql += " AND user_id=?"
            params.append(user_id)
        sql += " GROUP BY target_name, user_id ORDER BY kills DESC, user_id"
        return await self._all(sql, tuple(params))

    async def leaderboard(
        self, guild_id: int, start_ts: int, end_ts: int, limit: int = 15
    ) -> list[dict[str, Any]]:
        """Top players by kills (then amount) for the window."""
        return await self._all(
            "SELECT user_id, SUM(kills) AS kills, SUM(amount) AS amount FROM kill_logs "
            "WHERE guild_id=? AND occurred_at>=? AND occurred_at<? "
            "GROUP BY user_id ORDER BY kills DESC, amount DESC LIMIT ?",
            (guild_id, start_ts, end_ts, limit),
        )

    async def week_summary(self, guild_id: int, start_ts: int, end_ts: int) -> dict[str, Any]:
        """Aggregate stats for the closed week (for auto-reset confirmation)."""
        row = await self._one(
            "SELECT COUNT(DISTINCT user_id) AS members, COALESCE(SUM(kills),0) AS kills, "
            "COALESCE(SUM(amount),0) AS amount FROM kill_logs "
            "WHERE guild_id=? AND occurred_at>=? AND occurred_at<?",
            (guild_id, start_ts, end_ts),
        )
        pending = await self.pending_count(guild_id, start_ts, end_ts)
        return {
            "members": int(row["members"]) if row else 0,
            "kills": int(row["kills"]) if row else 0,
            "amount": int(row["amount"]) if row else 0,
            "pending": pending,
        }

    # ----------------------------------------------------------------- audit
    async def add_audit(
        self, guild_id: int, action: str, actor_id: int, detail: str = "", target_id: int | None = None
    ) -> None:
        assert self.conn is not None
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO audit_log (guild_id, action, actor_id, target_id, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, action, actor_id, target_id, detail[:1500], int(time.time())),
            )
            await self.conn.commit()

    async def recent_audit(self, guild_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM audit_log WHERE guild_id=? ORDER BY created_at DESC LIMIT ?",
            (guild_id, limit),
        )

    # ----------------------------------------------------------- rate limit
    async def record_claim_attempt(self, guild_id: int, user_id: int) -> None:
        assert self.conn is not None
        now = int(time.time())
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO claim_rate (guild_id, user_id, claimed_at) VALUES (?, ?, ?)",
                (guild_id, user_id, now),
            )
            # prune older than 2 hours
            await self.conn.execute(
                "DELETE FROM claim_rate WHERE claimed_at < ?", (now - 7200,)
            )
            await self.conn.commit()

    async def claim_count_since(self, guild_id: int, user_id: int, since_ts: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS n FROM claim_rate WHERE guild_id=? AND user_id=? AND claimed_at>=?",
            (guild_id, user_id, since_ts),
        )
        return int(row["n"]) if row else 0

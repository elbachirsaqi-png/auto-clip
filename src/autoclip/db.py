"""File d'attente SQLite : la table `moments` sert à la fois d'historique et de queue."""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from .models import Moment, Status

SCHEMA = """
CREATE TABLE IF NOT EXISTS moments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    detected_at     TEXT NOT NULL,
    score           REAL NOT NULL,
    status          TEXT NOT NULL,
    clip_url        TEXT,
    video_path      TEXT,
    transcript_path TEXT,
    frames_dir      TEXT,
    decision_json   TEXT,
    render_path     TEXT,
    error           TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    locked          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moments_status ON moments(status, locked);

CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    moment_id    INTEGER NOT NULL REFERENCES moments(id),
    target       TEXT NOT NULL,          -- youtube | tiktok
    external_id  TEXT,
    published_at TEXT NOT NULL
);

-- Moyennes glissantes par chaîne, pour reprendre après un redémarrage.
CREATE TABLE IF NOT EXISTS baselines (
    platform   TEXT NOT NULL,
    channel    TEXT NOT NULL,
    ewma       REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (platform, channel)
);
"""

_COLUMNS = [
    "platform", "channel", "detected_at", "score", "status", "clip_url", "video_path",
    "transcript_path", "frames_dir", "decision_json", "render_path", "error",
]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        # Libère les jobs restés verrouillés si le process a planté.
        await self.conn.execute("UPDATE moments SET locked = 0")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def insert_moment(self, m: Moment) -> int:
        row = m.model_dump(include=set(_COLUMNS), mode="json")
        cur = await self.conn.execute(
            f"INSERT INTO moments ({', '.join(_COLUMNS)}, updated_at) "
            f"VALUES ({', '.join('?' * len(_COLUMNS))}, ?)",
            [*(row[c] for c in _COLUMNS), _now()],
        )
        await self.conn.commit()
        return cur.lastrowid

    async def claim(self, status: Status) -> Moment | None:
        """Prend le plus ancien moment dans `status` et le verrouille."""
        async with self.conn.execute(
            "UPDATE moments SET locked = 1, attempts = attempts + 1, updated_at = ? "
            "WHERE id = (SELECT id FROM moments WHERE status = ? AND locked = 0 "
            "ORDER BY id LIMIT 1) RETURNING *",
            (_now(), status.value),
        ) as cur:
            row = await cur.fetchone()
        await self.conn.commit()
        if row is None:
            return None
        return Moment(**{k: row[k] for k in row.keys() if k in Moment.model_fields})

    async def advance(self, m: Moment, new_status: Status, **fields) -> None:
        """Enregistre les champs produits par l'étape et passe au statut suivant."""
        assignments = "".join(f", {k} = ?" for k in fields)
        values = [str(v) if v is not None else None for v in fields.values()]
        await self.conn.execute(
            f"UPDATE moments SET status = ?, locked = 0, attempts = 0, updated_at = ?{assignments} "
            "WHERE id = ?",
            [new_status.value, _now(), *values, m.id],
        )
        await self.conn.commit()

    async def fail(self, m: Moment, error: str, max_attempts: int = 3) -> bool:
        """Relâche le job pour un nouvel essai, ou le marque FAILED après trop d'échecs.

        Retourne True si le moment est désormais FAILED (abandonné).
        """
        async with self.conn.execute(
            "UPDATE moments SET locked = 0, error = ?, updated_at = ?, "
            "status = CASE WHEN attempts >= ? THEN ? ELSE status END WHERE id = ? "
            "RETURNING status",
            (error[:2000], _now(), max_attempts, Status.FAILED.value, m.id),
        ) as cur:
            row = await cur.fetchone()
        await self.conn.commit()
        return row is not None and row["status"] == Status.FAILED.value

    async def set_review_result(self, moment_id: int, approved: bool) -> bool:
        """Applique le verdict Telegram. False si le moment n'attendait plus de validation."""
        new_status = Status.APPROVED if approved else Status.REJECTED
        error = None if approved else "Rejeté sur Telegram"
        cur = await self.conn.execute(
            "UPDATE moments SET status = ?, error = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new_status.value, error, _now(), moment_id, Status.PENDING_REVIEW.value),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    async def statuses(self) -> dict[int, str]:
        async with self.conn.execute("SELECT id, status FROM moments") as cur:
            return {row["id"]: row["status"] for row in await cur.fetchall()}

    async def record_publication(self, moment_id: int, target: str, external_id: str) -> None:
        await self.conn.execute(
            "INSERT INTO publications (moment_id, target, external_id, published_at) "
            "VALUES (?, ?, ?, ?)",
            (moment_id, target, external_id, _now()),
        )
        await self.conn.commit()

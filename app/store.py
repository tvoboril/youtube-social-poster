"""SQLite-backed store for video IDs we've posted about or are waiting on.

WebSub also pushes notifications on title/description edits, so we must remember
which video IDs have already been posted to avoid duplicate Facebook posts.

We also remember upcoming (scheduled) streams: YouTube notifies when a broadcast
is scheduled, often weeks ahead, but usually not when it actually goes live. The
pending table lets the scheduled go-live checks survive restarts.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Iterator


class PostedStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posted (
                    video_id   TEXT PRIMARY KEY,
                    posted_at  INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending (
                    video_id         TEXT PRIMARY KEY,
                    scheduled_start  INTEGER NOT NULL
                )
                """
            )

    def already_posted(self, video_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posted WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row is not None

    def mark_posted(self, video_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO posted (video_id, posted_at) VALUES (?, ?)",
                (video_id, int(time.time())),
            )

    def set_pending(self, video_id: str, scheduled_start: int) -> None:
        """Record (or update) an upcoming video's scheduled start (unix seconds)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pending (video_id, scheduled_start) VALUES (?, ?)",
                (video_id, scheduled_start),
            )

    def get_pending(self, video_id: str) -> int | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT scheduled_start FROM pending WHERE video_id = ?", (video_id,)
            ).fetchone()
        return row[0] if row else None

    def all_pending(self) -> list[tuple[str, int]]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT video_id, scheduled_start FROM pending"
            ).fetchall()

    def clear_pending(self, video_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM pending WHERE video_id = ?", (video_id,))

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DONE_STATUSES = {"done", "duplicate", "skipped"}


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def message_status(self, chat_id: int, message_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM downloads WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        return str(row["status"]) if row else None

    def has_finished_message(self, chat_id: int, message_id: int) -> bool:
        status = self.message_status(chat_id, message_id)
        return status in DONE_STATUSES

    def find_done_media(self, media_key: str | None) -> sqlite3.Row | None:
        if not media_key:
            return None
        return self.conn.execute(
            """
            SELECT chat_id, message_id, file_path
            FROM downloads
            WHERE media_key = ? AND status = 'done' AND file_path IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (media_key,),
        ).fetchone()

    def mark_started(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        chat_title: str,
        file_path: Path | None,
        size: int,
    ) -> None:
        self._upsert(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            chat_title=chat_title,
            file_path=file_path,
            size=size,
            status="downloading",
            error=None,
        )

    def mark_done(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        chat_title: str,
        file_path: Path,
        size: int,
    ) -> None:
        self._upsert(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            chat_title=chat_title,
            file_path=file_path,
            size=size,
            status="done",
            error=None,
        )

    def mark_duplicate(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        chat_title: str,
        existing_file_path: Path,
        size: int,
    ) -> None:
        self._upsert(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            chat_title=chat_title,
            file_path=existing_file_path,
            size=size,
            status="duplicate",
            error=None,
        )

    def mark_skipped(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        chat_title: str,
        reason: str,
        size: int,
    ) -> None:
        self._upsert(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            chat_title=chat_title,
            file_path=None,
            size=size,
            status="skipped",
            error=reason,
        )

    def mark_failed(
        self,
        *,
        chat_id: int,
        message_id: int,
        media_key: str | None,
        chat_title: str,
        file_path: Path | None,
        size: int,
        error: str,
    ) -> None:
        self._upsert(
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            chat_title=chat_title,
            file_path=file_path,
            size=size,
            status="failed",
            error=error[:2000],
        )

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                media_key TEXT,
                chat_title TEXT,
                file_path TEXT,
                size INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            );

            CREATE INDEX IF NOT EXISTS idx_downloads_media_key
            ON downloads(media_key);

            CREATE INDEX IF NOT EXISTS idx_downloads_status
            ON downloads(status);
            """
        )
        self.conn.commit()

    def _upsert(self, **values: Any) -> None:
        file_path = values["file_path"]
        file_path_text = str(file_path) if file_path is not None else None
        self.conn.execute(
            """
            INSERT INTO downloads (
                chat_id, message_id, media_key, chat_title, file_path, size, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                media_key = excluded.media_key,
                chat_title = excluded.chat_title,
                file_path = excluded.file_path,
                size = excluded.size,
                status = excluded.status,
                error = excluded.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                values["chat_id"],
                values["message_id"],
                values["media_key"],
                values["chat_title"],
                file_path_text,
                values["size"],
                values["status"],
                values["error"],
            ),
        )
        self.conn.commit()

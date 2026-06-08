from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.custom.message import Message

from .config import AppConfig
from .db import StateStore


LOG = logging.getLogger(__name__)


INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTISPACE = re.compile(r"\s+")
RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class MediaInfo:
    chat_id: int
    chat_title: str
    message_id: int
    media_key: str | None
    filename: str
    extension: str
    size: int


class TelegramNasDownloader:
    def __init__(self, config: AppConfig, client: TelegramClient, store: StateStore) -> None:
        self.config = config
        self.client = client
        self.store = store
        self.semaphore = asyncio.Semaphore(config.download.workers)
        self.tasks: set[asyncio.Task[None]] = set()
        self.chat_entities: list[object] = []

    async def resolve_chats(self) -> None:
        self.chat_entities = []
        for chat in self.config.download.chats:
            entity = await self.client.get_entity(chat)
            self.chat_entities.append(entity)
            title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat)
            LOG.info("Watching chat: %s", title)

    async def scan_history(self, limit: int | None = None) -> None:
        if not self.chat_entities:
            await self.resolve_chats()

        message_limit = self.config.download.history_limit if limit is None else limit
        if message_limit <= 0:
            LOG.info("History scan skipped because limit is 0.")
            return

        LOG.info("Scanning up to %s historical messages per chat.", message_limit)
        for entity in self.chat_entities:
            async for message in self.client.iter_messages(entity, limit=message_limit):
                await self.download_message(message)

    async def run_forever(self) -> None:
        await self.resolve_chats()
        if self.config.download.scan_on_start:
            await self.scan_history()

        @self.client.on(events.NewMessage(chats=self.chat_entities))
        async def _handler(event: events.NewMessage.Event) -> None:
            task = asyncio.create_task(self.download_message(event.message))
            self.tasks.add(task)
            task.add_done_callback(self._finish_task)

        LOG.info("Listening for new Telegram video messages. Press Ctrl+C to stop.")
        await self.client.run_until_disconnected()

    async def wait_for_background_tasks(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def download_message(self, message: Message) -> None:
        if not message or not message.media:
            return
        if not self._looks_like_video(message):
            return

        info = await self._media_info(message)
        if self.store.has_finished_message(info.chat_id, info.message_id):
            LOG.debug("Already processed %s/%s.", info.chat_id, info.message_id)
            return

        skip_reason = self._size_skip_reason(info)
        if skip_reason:
            self.store.mark_skipped(
                chat_id=info.chat_id,
                message_id=info.message_id,
                media_key=info.media_key,
                chat_title=info.chat_title,
                reason=skip_reason,
                size=info.size,
            )
            LOG.info("Skipped %s/%s: %s", info.chat_id, info.message_id, skip_reason)
            return

        if self.config.download.dedupe_by_media:
            existing = self.store.find_done_media(info.media_key)
            if existing and Path(existing["file_path"]).exists():
                self.store.mark_duplicate(
                    chat_id=info.chat_id,
                    message_id=info.message_id,
                    media_key=info.media_key,
                    chat_title=info.chat_title,
                    existing_file_path=Path(existing["file_path"]),
                    size=info.size,
                )
                LOG.info(
                    "Skipped duplicate media %s/%s; existing file is %s.",
                    info.chat_id,
                    info.message_id,
                    existing["file_path"],
                )
                return

        target_path = self._target_path(message, info)
        if target_path.exists() and not self.config.download.overwrite:
            self.store.mark_done(
                chat_id=info.chat_id,
                message_id=info.message_id,
                media_key=info.media_key,
                chat_title=info.chat_title,
                file_path=target_path,
                size=target_path.stat().st_size,
            )
            LOG.info("File already exists, marked done: %s", target_path)
            return

        async with self.semaphore:
            await self._download_to_path(message, info, target_path)

    async def _download_to_path(self, message: Message, info: MediaInfo, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.download.tmp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.config.download.tmp_dir / f"{info.chat_id}_{info.message_id}_{uuid4().hex}.download"

        self.store.mark_started(
            chat_id=info.chat_id,
            message_id=info.message_id,
            media_key=info.media_key,
            chat_title=info.chat_title,
            file_path=target_path,
            size=info.size,
        )

        started_at = time.monotonic()
        last_log_at = 0.0

        def progress(received: int, total: int) -> None:
            nonlocal last_log_at
            now = time.monotonic()
            if now - last_log_at < 30 and received != total:
                return
            last_log_at = now
            total_text = _format_bytes(total) if total else "unknown"
            LOG.info(
                "Downloading %s/%s: %s / %s",
                info.chat_id,
                info.message_id,
                _format_bytes(received),
                total_text,
            )

        try:
            downloaded = await message.download_media(file=str(temp_path), progress_callback=progress)
            downloaded_path = Path(downloaded) if downloaded else temp_path
            if not downloaded_path.exists():
                raise RuntimeError("Telegram returned no downloaded file.")

            if target_path.exists() and self.config.download.overwrite:
                target_path.unlink()
            move_downloaded_file(downloaded_path, target_path)

            elapsed = max(0.001, time.monotonic() - started_at)
            size = target_path.stat().st_size
            self.store.mark_done(
                chat_id=info.chat_id,
                message_id=info.message_id,
                media_key=info.media_key,
                chat_title=info.chat_title,
                file_path=target_path,
                size=size,
            )
            LOG.info("Saved %s (%s, %.2f MB/s)", target_path, _format_bytes(size), size / elapsed / 1024 / 1024)
        except FloodWaitError as exc:
            self.store.mark_failed(
                chat_id=info.chat_id,
                message_id=info.message_id,
                media_key=info.media_key,
                chat_title=info.chat_title,
                file_path=target_path,
                size=info.size,
                error=f"Telegram flood wait: retry after {exc.seconds} seconds",
            )
            LOG.warning("Telegram asked to wait %s seconds; sleeping before continuing.", exc.seconds)
            await asyncio.sleep(exc.seconds)
        except Exception as exc:
            self.store.mark_failed(
                chat_id=info.chat_id,
                message_id=info.message_id,
                media_key=info.media_key,
                chat_title=info.chat_title,
                file_path=target_path,
                size=info.size,
                error=str(exc),
            )
            LOG.exception("Failed to download %s/%s.", info.chat_id, info.message_id)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    async def _media_info(self, message: Message) -> MediaInfo:
        chat = await message.get_chat()
        chat_title = (
            getattr(chat, "title", None)
            or getattr(chat, "username", None)
            or getattr(chat, "first_name", None)
            or str(message.chat_id)
        )
        extension = self._extension(message)
        filename = self._filename(message, extension)
        return MediaInfo(
            chat_id=int(message.chat_id or 0),
            chat_title=str(chat_title),
            message_id=int(message.id),
            media_key=self._media_key(message),
            filename=filename,
            extension=extension,
            size=int(getattr(message.file, "size", 0) or 0),
        )

    def _target_path(self, message: Message, info: MediaInfo) -> Path:
        safe_chat_title = safe_path_segment(info.chat_title)
        safe_filename = safe_path_segment(info.filename)
        relative = self.config.download.layout.format(
            chat_id=info.chat_id,
            chat_title=safe_chat_title,
            message_id=info.message_id,
            date=message.date,
            filename=safe_filename,
            ext=info.extension,
        )
        target = (self.config.download.destination / relative).resolve()
        destination = self.config.download.destination.resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise ValueError(f"Resolved target path escapes destination: {target}") from exc
        return target

    def _looks_like_video(self, message: Message) -> bool:
        file = getattr(message, "file", None)
        mime_type = str(getattr(file, "mime_type", "") or "").lower()
        if mime_type.startswith("video/"):
            return True

        extension = self._extension(message)
        if extension in self.config.download.video_extensions:
            return True

        return bool(getattr(message, "video", None))

    def _extension(self, message: Message) -> str:
        file = getattr(message, "file", None)
        raw = str(getattr(file, "ext", "") or "")
        if not raw:
            name = str(getattr(file, "name", "") or "")
            raw = Path(name).suffix
        extension = raw.lower().strip()
        if not extension:
            return ".mp4"
        return extension if extension.startswith(".") else f".{extension}"

    def _filename(self, message: Message, extension: str) -> str:
        file = getattr(message, "file", None)
        name = str(getattr(file, "name", "") or "").strip()
        if not name:
            return f"{message.id}{extension}"
        if not Path(name).suffix:
            return f"{name}{extension}"
        return name

    def _media_key(self, message: Message) -> str | None:
        media = getattr(message, "media", None)
        document = getattr(media, "document", None)
        if document is not None:
            return f"document:{document.id}"
        photo = getattr(media, "photo", None)
        if photo is not None:
            return f"photo:{photo.id}"
        return None

    def _size_skip_reason(self, info: MediaInfo) -> str | None:
        if self.config.download.min_bytes and info.size and info.size < self.config.download.min_bytes:
            return f"smaller than min_bytes ({info.size} < {self.config.download.min_bytes})"
        if self.config.download.max_bytes and info.size and info.size > self.config.download.max_bytes:
            return f"larger than max_bytes ({info.size} > {self.config.download.max_bytes})"
        return None

    def _finish_task(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        try:
            task.result()
        except Exception:
            LOG.exception("Background download task failed.")


def safe_path_segment(value: str, fallback: str = "untitled") -> str:
    sanitized = INVALID_PATH_CHARS.sub("_", value)
    sanitized = MULTISPACE.sub(" ", sanitized).strip(" .")
    if not sanitized:
        sanitized = fallback
    if sanitized.upper() in RESERVED_WINDOWS_NAMES:
        sanitized = f"_{sanitized}"
    return sanitized[:160]


def move_downloaded_file(downloaded_path: Path, target_path: Path) -> None:
    try:
        os.replace(downloaded_path, target_path)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        LOG.warning(
            "Temporary path and target path are on different filesystems; copying %s to %s.",
            downloaded_path,
            target_path,
        )
        shutil.move(str(downloaded_path), str(target_path))


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"

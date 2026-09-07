"""Cross-platform file logging with persistent, uncompressed rotation."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from logging.handlers import BaseRotatingHandler
from pathlib import Path

DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024


def configured_log_level(value: str | None = None) -> str:
    """Return a validated stdlib logging level from HTX_LOG_LEVEL."""

    level = value if value is not None else os.getenv("HTX_LOG_LEVEL", "INFO")
    level = level.strip().upper()
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if level not in allowed:
        raise ValueError(
            "HTX_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
        )
    return level


def configured_log_max_bytes(value: str | None = None) -> int:
    """Return the size threshold; zero delegates rotation to an external tool."""

    raw = value if value is not None else os.getenv("HTX_LOG_MAX_BYTES")
    if raw is None or not raw.strip():
        return DEFAULT_LOG_MAX_BYTES
    try:
        size = int(raw)
    except ValueError as exc:
        raise ValueError("HTX_LOG_MAX_BYTES must be a non-negative integer") from exc
    if size < 0:
        raise ValueError("HTX_LOG_MAX_BYTES must be a non-negative integer")
    return size


def configured_log_directory(value: str | None = None) -> Path:
    """Resolve the configured log directory without platform-specific paths."""

    raw = value if value is not None else os.getenv("HTX_LOG_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".htxmcp"


class PersistentRotatingFileHandler(BaseRotatingHandler):
    """Rotate by size without compressing or deleting historical files."""

    def __init__(self, filename: Path, *, max_bytes: int, delay: bool = True):
        self.max_bytes = max_bytes
        self._rollover_sequence = 0
        super().__init__(filename, mode="a", encoding="utf-8", delay=delay)

    def _open(self):
        stream = super()._open()
        try:
            Path(self.baseFilename).chmod(0o600)
        except OSError:
            pass
        return stream

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if self.max_bytes <= 0:
            return False
        message_size = len((self.format(record) + self.terminator).encode("utf-8"))
        try:
            current_size = Path(self.baseFilename).stat().st_size
        except FileNotFoundError:
            current_size = 0
        return current_size > 0 and current_size + message_size > self.max_bytes

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        source = Path(self.baseFilename)
        if source.exists() and source.stat().st_size:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            while True:
                self._rollover_sequence += 1
                destination = source.with_name(
                    f"{source.stem}.{stamp}.{os.getpid()}.{self._rollover_sequence}.log"
                )
                if not destination.exists():
                    source.replace(destination)
                    break
        if not self.delay:
            self.stream = self._open()


def configure_file_logging() -> tuple[str, Path, int]:
    """Send process logs to ~/.htxmcp and leave stderr untouched."""

    level = configured_log_level()
    directory = configured_log_directory()
    max_bytes = configured_log_max_bytes()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass

    path = directory / "htx-mcp.log"
    handler = PersistentRotatingFileHandler(path, max_bytes=max_bytes)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.basicConfig(level=level, handlers=[handler], force=True)
    return level, path, max_bytes


LOG_LEVEL, LOG_PATH, LOG_MAX_BYTES = configure_file_logging()

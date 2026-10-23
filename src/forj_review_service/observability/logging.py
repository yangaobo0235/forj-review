"""One console/file pipeline for application and Uvicorn lifecycle events."""

import copy
import logging
import os
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from forj_review_service.config.settings import load_dotenv
from forj_review_service.observability.context import current_log_context


class SafeFormatter(logging.Formatter):
    """Bounded single-line events and stack locations without exception payloads."""

    def format(self, record: logging.LogRecord) -> str:
        item = copy.copy(record)
        text = item.getMessage()
        text = re.sub(r"https?://[^\s\"'<>]+", "[url-redacted]", text)
        text = re.sub(
            r"(?i)(bearer\s+|(?:api[_-]?key|token|password|authorization)=)[^\s,;]+",
            r"\1[redacted]",
            text,
        )
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[key-redacted]", text)
        text = re.sub(
            r"\b(?:\d{17}[\dXx]|1[3-9]\d{9}|[A-HJ-NPR-Z0-9]{17})\b", "[value-redacted]", text
        )
        item.msg = text.replace("\r", "\\r").replace("\n", "\\n")[:8000]
        item.args = ()
        item.exc_text = None
        item.stack_info = None
        item.context_fields = " ".join(
            f"{key}={value}" for key, value in current_log_context().items()
        )
        return super().format(item).replace("\r", "\\r").replace("\n", "\\n")

    def formatException(self, exc_info) -> str:
        frames = traceback.extract_tb(exc_info[2])
        locations = " > ".join(
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames[-12:]
        )
        return f"exception_type={exc_info[0].__name__} stack={locations or '-'}"


class BoundedDailyFileHandler(TimedRotatingFileHandler):
    """Rotate at midnight or size limit, sharing a fixed archive count."""

    def __init__(self, filename: str, *, max_bytes: int, backup_count: int):
        self.max_bytes = max_bytes
        super().__init__(filename, when="midnight", backupCount=backup_count, encoding="utf-8")
        if os.name != "nt":
            try:
                os.chmod(filename, 0o640)
            except OSError:
                self.close()
                raise

    def handleError(self, record: logging.LogRecord) -> None:
        # Logging's default handler prints raw record arguments on disk/rotation
        # errors. Keep failure visible without leaking those arguments.
        error = sys.exc_info()[0]
        print(
            f"review log file write failed error_type={error.__name__ if error else 'unknown'}; check disk space and permissions",
            file=sys.stderr,
        )

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if super().shouldRollover(record):
            return True
        return bool(
            self.stream
            and self.stream.tell() > 0
            and self.stream.tell() + len((self.format(record) + "\n").encode("utf-8"))
            >= self.max_bytes
        )

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None
        if os.path.exists(self.baseFilename):
            stamp = datetime.now().strftime("%Y-%m-%d.%H-%M-%S.%f")
            self.rotate(self.baseFilename, f"{self.baseFilename}.{stamp}")
        for filename in self.getFilesToDelete():
            os.remove(filename)
        self.stream = self._open()
        if os.name != "nt":
            os.chmod(self.baseFilename, 0o640)
        self.rolloverAt = self.computeRollover(int(time.time()))

    def getFilesToDelete(self) -> list[str]:
        path = Path(self.baseFilename)
        archives = sorted(
            item
            for item in path.parent.glob(path.name + ".*")
            if re.fullmatch(
                re.escape(path.name) + r"\.\d{4}-\d{2}-\d{2}(?:\.\d{2}-\d{2}-\d{2}\.\d{6})?",
                item.name,
            )
            and item.is_file()
            and not item.is_symlink()
        )
        return [str(item) for item in archives[: -self.backupCount]] if self.backupCount else []


def _positive_env(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def configure_log_file(handler_logger: logging.Logger) -> None:
    """Append a bounded file sink. Production can require it instead of silent fallback."""
    load_dotenv()
    log_dir = os.getenv("REVIEW_LOG_DIR", "").strip()
    required = os.getenv("REVIEW_LOG_REQUIRED", "false").lower() in {"1", "true", "yes"}
    if not log_dir:
        if required:
            raise RuntimeError("REVIEW_LOG_DIR is required")
        return
    target = os.path.abspath(os.path.join(log_dir, "review-service.log"))
    if any(
        getattr(existing, "baseFilename", None) == target for existing in handler_logger.handlers
    ):
        return
    max_mb = _positive_env("REVIEW_LOG_MAX_MB", 10, 100)
    backups = _positive_env("REVIEW_LOG_BACKUP_COUNT", 30, 365)
    try:
        os.makedirs(log_dir, exist_ok=True)
        handler = BoundedDailyFileHandler(
            target, max_bytes=max_mb * 1024 * 1024, backup_count=backups
        )
    except OSError as exc:
        handler_logger.warning(
            "Review log file disabled: error_type=%s required=%s", type(exc).__name__, required
        )
        if required:
            raise RuntimeError("Required review log file is not writable") from None
        return
    handler.setFormatter(
        SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(context_fields)s")
    )
    handler_logger.addHandler(handler)
    handler_logger.info(
        "Review log file enabled: rotation=daily_or_size max_mb=%d backup_count=%d", max_mb, backups
    )


@contextmanager
def configured_logging():
    """Scoped bootstrap configuration; shutdown restores embedding/test loggers."""
    load_dotenv()
    level = os.getenv("REVIEW_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError("REVIEW_LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")
    app_logger = logging.getLogger("forj_review_service")
    server_logger = logging.getLogger("uvicorn.error")
    access_logger = logging.getLogger("uvicorn.access")
    saved = [
        (target, list(target.handlers), target.level, target.propagate)
        for target in (app_logger, server_logger)
    ]
    access_disabled = access_logger.disabled
    console = logging.StreamHandler()
    console.setFormatter(
        SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(context_fields)s")
    )
    owned: list[logging.Handler] = [console]
    try:
        for target in (app_logger, server_logger):
            target.handlers = [console]
            target.setLevel(level)
            target.propagate = False
        access_logger.disabled = True
        configure_log_file(app_logger)
        owned = list(app_logger.handlers)
        server_logger.handlers = list(owned)
        yield
    finally:
        for target, handlers, previous_level, propagate in saved:
            target.handlers, target.level, target.propagate = handlers, previous_level, propagate
        access_logger.disabled = access_disabled
        for handler in owned:
            handler.close()

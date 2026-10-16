"""Cross-process, reentrant coordination for local configuration snapshots."""

import hashlib
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from filelock import FileLock

_locks: dict[str, FileLock] = {}
_guard = Lock()
PENDING_COMMIT = ".configuration-pending"
def _kernel_version():
    root = Path(__file__).resolve().parents[1]
    hasher = hashlib.sha256()
    for group in ("businesses", "capabilities", "config", "contracts", "evidence", "runtime", "presentation"):
        for path in sorted((root / group).rglob("*.py")):
            hasher.update(path.relative_to(root).as_posix().encode())
            hasher.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return "configuration-1:" + hasher.hexdigest()[:16]


KERNEL_VERSION = _kernel_version()


@contextmanager
def configuration_lock(directory: Path, *, allow_pending: bool = False):
    directory = directory.resolve()
    key = str(directory)
    with _guard:
        lock = _locks.setdefault(key, FileLock(str(directory.parent / f".{directory.name}.lock")))
    with lock.acquire(timeout=15):
        if not allow_pending and (directory / PENDING_COMMIT).exists():
            raise ValueError("COMMIT_INCOMPLETE：配置提交未完成，请使用配置服务恢复命令")
        yield

"""Cross-process, reentrant coordination for local configuration snapshots."""

import hashlib
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from weakref import WeakValueDictionary

from filelock import FileLock

_locks: WeakValueDictionary[str, FileLock] = WeakValueDictionary()
_guard = Lock()


def _kernel_version():
    root = Path(__file__).resolve().parents[2]
    hasher = hashlib.sha256()
    for group in (
        "businesses",
        "capabilities",
        "config",
        "contracts",
        "evidence",
        "runtime",
        "presentation",
    ):
        for path in sorted((root / group).rglob("*.py")):
            hasher.update(path.relative_to(root).as_posix().encode())
            hasher.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return "configuration-1:" + hasher.hexdigest()[:16]


KERNEL_VERSION = _kernel_version()


@contextmanager
def configuration_lock(directory: Path):
    directory = directory.resolve()
    key = str(directory)
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            lock = FileLock(str(directory.parent / f".{directory.name}.lock"))
            _locks[key] = lock
    with lock.acquire(timeout=15):
        yield

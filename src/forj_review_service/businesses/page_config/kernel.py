"""Fingerprint of the audit engine used by runtime diagnostics."""

import hashlib
from pathlib import Path


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
        for path in sorted(p for p in (root / group).rglob("*") if p.suffix in {".py", ".json"}):
            hasher.update(path.relative_to(root).as_posix().encode())
            hasher.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return "configuration-1:" + hasher.hexdigest()[:16]


KERNEL_VERSION = _kernel_version()

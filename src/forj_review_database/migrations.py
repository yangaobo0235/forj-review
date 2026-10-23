"""Append-only SQL migration registry; execution belongs to explicit maintenance."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from forj_review_database.schema import BASELINE_VERSION, SCHEMA_SQL, schema_checksum


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    checksum: str

    def read_sql(self) -> str:
        path = self.path if self.path.is_absolute() else Path(__file__).parent / self.path
        sql = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(sql).hexdigest() != self.checksum:
            raise ValueError(f"迁移 {self.version} 文件摘要不匹配，已发布迁移不可修改")
        return sql.decode("utf-8")


# Append Migration(5, name, path, pinned_sha256), etc. Never edit published entries.
UPGRADES: tuple[Migration, ...] = (
    Migration(
        5,
        "configuration_tasks",
        Path("migration_sql/005_configuration_tasks.sql"),
        "1d1871cd917f5dd4d995c1a442f92fad499cdf4bf55f70dc9def306e0790a90b",
    ),
    Migration(
        6,
        "capture_evidence",
        Path("migration_sql/006_capture_evidence.sql"),
        "55817167b4fac141344245bef46289e8eba04a162b174fd42c14e0066654d86f",
    ),
    Migration(
        7,
        "configuration_workspace",
        Path("migration_sql/007_configuration_workspace.sql"),
        "b2a6e90086b20127dcb865d6751d0e7bca5331e01994f77ab4246a8865b1fd81",
    ),
)


def migration_history() -> tuple[Migration, ...]:
    history = (Migration(BASELINE_VERSION, "v4_baseline", SCHEMA_SQL, schema_checksum()), *UPGRADES)
    for expected, migration in enumerate(history, BASELINE_VERSION):
        if migration.version != expected:
            raise ValueError("迁移注册必须从 v4 基线连续递增")
        migration.read_sql()
    return history


def validate_history(
    rows: Sequence,
    history: tuple[Migration, ...],
    *,
    require_current: bool = False,
) -> int:
    # v3 records predate this immutable registry and may remain after the v3 upgrade.
    managed = [(version, checksum) for version, checksum in rows if version >= BASELINE_VERSION]
    expected = [(migration.version, migration.checksum) for migration in history]
    if not managed or managed != expected[: len(managed)]:
        raise ValueError("PostgreSQL 存储版本或迁移历史摘要不兼容")
    if require_current and managed != expected:
        raise ValueError("PostgreSQL 存储版本落后，请使用显式迁移工具")
    return managed[-1][0]

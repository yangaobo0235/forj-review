"""PostgreSQL sessions, events and idempotency receipts in one transaction."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from psycopg.types.json import Jsonb

from forj_review_config_service.integrations.database import Database


def read_files(directory: Path) -> dict[str, str]:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("配置目录不能包含符号链接")
        if path.is_file() and path.suffix == ".json":
            files[path.relative_to(directory).as_posix()] = path.read_text(encoding="utf-8-sig")
    return files


def write_files(directory: Path, files: dict[str, str]):
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        relative = PurePosixPath(name)
        if (
            not relative.parts
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or not isinstance(content, str)
        ):
            raise ValueError("数据库中的配置文件路径或内容无效")
        target = directory.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


class PageSnapshots:
    def __init__(self, database: Database):
        self.database = database

    def get(self) -> dict[str, str]:
        with self.database.connect() as db:
            row = db.execute("SELECT files FROM page_snapshots WHERE id=1").fetchone()
        if row is None:
            raise ValueError("PostgreSQL 页面配置快照缺失，请核对数据库与配置空间")
        return row[0]

    @contextmanager
    def edit(self):
        with self.database.connect() as db:
            row = db.execute("SELECT files FROM page_snapshots WHERE id=1 FOR UPDATE").fetchone()
            if row is None:
                raise ValueError("PostgreSQL 尚未导入页面配置")
            files = row[0]
            before = json.dumps(files, sort_keys=True)
            yield files
            if json.dumps(files, sort_keys=True) != before:
                db.execute(
                    "UPDATE page_snapshots SET files=%s,revision=revision+1 WHERE id=1",
                    (Jsonb(files),),
                )

"""校验离线车型导出包后安装 SQLite；无需连接源数据库。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from forj_review_assistant.config.vehicle_models import DEFAULT_CATALOG_PATH  # noqa: E402
from forj_review_assistant.integrations.vehicle_models import _SQL  # noqa: E402

FILES = {"vehicle-model-catalog.sqlite", "manifest.json", "example-matches.json",
         "README.md", "sha256.json"}
TABLES = {"forj_vehicle_brand", "forj_vehicle_series", "forj_vehicle_model",
          "forj_vehicle_model_config"}


def install(package: Path, destination: Path) -> dict:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalog-import-", dir=destination.parent) as folder:
        staging = Path(folder)
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            if set(names) != FILES or len(names) != len(FILES):
                raise ValueError("车型数据包文件列表不完整或包含额外文件")
            if sum(info.file_size for info in archive.infolist()) > 2 * 1024 ** 3:
                raise ValueError("车型数据包过大，请检查导出格式")
            checksums = json.loads(archive.read("sha256.json"))
            if not isinstance(checksums, dict) or set(checksums) != FILES - {"sha256.json"}:
                raise ValueError("车型数据包校验清单不完整")
            for name in sorted(checksums):
                with archive.open(name) as source, (staging / name).open("wb") as target:
                    digest = hashlib.sha256()
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        target.write(chunk)
                if digest.hexdigest() != checksums[name]:
                    raise ValueError("车型数据包校验码不一致，原目录未替换")
        manifest = json.loads((staging / "manifest.json").read_text("utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("format") != "forj-vehicle-model-catalog"
                or manifest.get("version") != 1 or set(manifest.get("tables", {})) != TABLES):
            raise ValueError("车型目录版本或表结构不受支持")
        database = staging / "vehicle-model-catalog.sqlite"
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("车型目录 SQLite 完整性检查失败")
            stored = connection.execute("SELECT value FROM catalog_metadata WHERE key='manifest'").fetchone()
            if not stored or json.loads(stored[0]) != manifest:
                raise ValueError("目录内置版本与导出清单不一致")
            for table in sorted(TABLES):
                count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if count != manifest["tables"][table]["rows"]:
                    raise ValueError("目录记录数量与清单不一致")
            count = connection.execute("SELECT COUNT(*) FROM catalog_keys").fetchone()[0]
            if count != manifest["validation"]["active_models"]:
                raise ValueError("车型检索索引数量与清单不一致")
            index = connection.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='catalog_codes'").fetchone()
            if not index:
                raise ValueError("车型目录缺少型号检索索引")
            # 提前验证当前查询所需列和关联，避免安装后才发现目录结构不兼容。
            connection.execute("EXPLAIN QUERY PLAN " + _SQL, ("MODEL00", "ENGINE00", 0, 0, "牵引车")).fetchall()
        # 完整校验后才替换运行目录；旧数据副本与最初的导出包都保留。
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".previous.sqlite"))
        os.replace(database, destination)
        (destination.parent / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--destination", type=Path, default=DEFAULT_CATALOG_PATH)
    args = parser.parse_args()
    try:
        manifest = install(args.package, args.destination)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, zipfile.BadZipFile):
        print("离线目录安装失败：请检查数据包完整性、版本或目录权限；运行中的目录不会使用未通过校验的数据。", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps({"installed": str(args.destination.resolve()), "format": "SQLite",
                      "exported_at": manifest["exported_at"],
                      "active_models": manifest["validation"]["active_models"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

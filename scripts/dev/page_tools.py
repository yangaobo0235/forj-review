"""Explicit export/publication of validated page snapshots in the shared database."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from forj_review_config_service.config.database import database_url_from_config
from forj_review_config_service.integrations.configuration_store.postgres import (
    PageSnapshots,
    read_files,
    write_files,
)
from forj_review_config_service.integrations.database import Database


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def publish(store: PageSnapshots, source: Path, expected_digest: str, backup: Path):
    from forj_review_service.businesses.page_config.history import VersionHistory
    from forj_review_service.runtime.configuration.service import validated_configuration

    incoming = read_files(source)
    candidate, _ = validated_configuration(source)
    with store.edit() as current, TemporaryDirectory(prefix="forj-publish-") as tmp:
        if fingerprint(current) != expected_digest:
            raise ValueError("目标配置已变化，请重新导出、比较并确认目标摘要")
        backup.mkdir(parents=True, exist_ok=False)
        write_files(backup, current)
        previous, _ = validated_configuration(backup)
        # Historical versions may change activation, but their declared content cannot change/disappear.
        def identities(pages):
            return {(d.page_id, d.version): d.model_dump(mode="json", exclude_unset=True,
                                                       exclude={"active"}) for _, d in pages.sources}

        old, new = identities(previous), identities(candidate)
        if any(identity not in new or new[identity] != value for identity, value in old.items()):
            raise ValueError("发布必须保留目标已有的全部配置版本及其原始内容")
        merged = dict(incoming)
        history_name = ".history/versions.json"
        for name, value in current.items():
            if not name.startswith((".history/", ".transactions/")) or name == history_name:
                continue
            if name in merged and merged[name] != value:
                raise ValueError("发布包与目标的配置历史或保存回执冲突")
            merged[name] = value
        old_history = VersionHistory.model_validate_json(current.get(history_name, "{}"))
        new_history = VersionHistory.model_validate_json(incoming.get(history_name, "{}"))
        if any(key in new_history.versions and new_history.versions[key] != value
               for key, value in old_history.versions.items()):
            raise ValueError("发布包的历史版本摘要与目标冲突")
        if history_name in current or history_name in incoming:
            merged[history_name] = VersionHistory(versions={**old_history.versions,
                                                           **new_history.versions}).model_dump_json(indent=2) + "\n"
        staged = Path(tmp) / "pages"
        staged.mkdir()
        write_files(staged, merged)
        validated_configuration(staged)
        current.clear()
        current.update(merged)
    return {"digest": fingerprint(merged), "files": len(merged), "backup": str(backup.resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "publish"])
    parser.add_argument("--schema", default="public")
    parser.add_argument("--database-env", default="FORJ_DATABASE_URL")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--expected-digest")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    url = os.getenv(args.database_env, "").strip() or database_url_from_config()
    if not url:
        parser.error(f"请配置环境变量 {args.database_env}")
    store = PageSnapshots(Database(url, args.schema))
    if args.action == "export":
        files = store.get()
        args.directory.mkdir(parents=True, exist_ok=False)
        write_files(args.directory, files)
        result = {"digest": fingerprint(files), "files": len(files)}
    else:
        if not args.expected_digest or not args.backup_dir:
            parser.error("publish 需要 --expected-digest 和 --backup-dir")
        result = publish(store, args.directory, args.expected_digest, args.backup_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

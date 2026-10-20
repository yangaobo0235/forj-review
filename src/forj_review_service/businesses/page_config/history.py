"""Opt-in local version history; no server publishing or live mutation."""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

from pydantic import StringConstraints

from forj_review_service.businesses.page_config.files import configuration_lock
from forj_review_service.businesses.page_config.inspection import (
    digest,
    effective_configuration,
)
from forj_review_service.businesses.page_config.schema import Declaration, PageDefinition

if TYPE_CHECKING:
    from forj_review_service.businesses.page_config.loader import ConfiguredPages

Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class RecordedVersion(Declaration):
    page_id: str
    version: str
    source_digest: Digest
    effective_digest: Digest


class VersionHistory(Declaration):
    schema_version: str = "1.0"
    versions: dict[str, RecordedVersion] = {}


def _history(directory: Path) -> VersionHistory:
    path = directory / ".history" / "versions.json"
    if not path.exists():
        return VersionHistory()
    try:
        return parse_history(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"本地配置历史 {path} 无效：{exc}") from exc


def parse_history(text: str) -> VersionHistory:
    try:
        history = VersionHistory.model_validate_json(text)
        if history.schema_version != "1.0" or any(
            key != digest([entry.page_id, entry.version]) for key, entry in history.versions.items()
        ):
            raise ValueError("历史身份或版本格式无效")
        return history
    except ValueError as exc:
        raise ValueError(f"配置历史无效：{exc}") from exc


def _entries(pages: "ConfiguredPages") -> dict[str, RecordedVersion]:
    sources = {(d.page_id, d.version): d for _, d in pages.sources}
    result = {}
    for profile in pages.profiles:
        identity = (profile.page_id, profile.version)
        source = sources[identity].model_dump(
            mode="json",
            exclude_unset=True,
            exclude={"active"},
        )
        result[digest(identity)] = RecordedVersion(
            page_id=profile.page_id,
            version=profile.version,
            source_digest=digest(source),
            effective_digest=digest(effective_configuration(profile)),
        )
    return result


def validate_recorded_versions(directory: Path, pages: "ConfiguredPages") -> None:
    _validate_versions(_history(directory), pages)


def validate_snapshot_versions(files: Mapping[str, str], pages: "ConfiguredPages") -> None:
    text = files.get(".history/versions.json")
    _validate_versions(parse_history(text) if text is not None else VersionHistory(), pages)


def _validate_versions(history: VersionHistory, pages: "ConfiguredPages") -> None:
    for key, entry in _entries(pages).items():
        previous = history.versions.get(key)
        if previous is not None and previous != entry:
            raise ValueError(
                f"页面 {entry.page_id}/{entry.version} 已有本地快照，内容或模板发生变化；"
                "请使用新版本，不要覆盖历史版本"
            )


def record_snapshot(directory: Path, pages: "ConfiguredPages") -> dict:
    """Record a validated baseline. Activation is mutable; version content is not."""
    with configuration_lock(directory):
        return _record_snapshot(directory, pages)


def _record_snapshot(directory: Path, pages: "ConfiguredPages") -> dict:
    current = {
        path.name: PageDefinition.model_validate_json(
            path.read_text(encoding="utf-8-sig")
        ).model_dump(mode="json", exclude_unset=True)
        for path in directory.glob("*.json")
    }
    expected = {
        name: definition.model_dump(mode="json", exclude_unset=True)
        for name, definition in pages.sources
    }
    if current != expected:
        raise ValueError("配置目录已变化，请重新校验后保存快照")
    root = directory / ".history"
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "write.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            "本地配置快照正在写入；若上次异常退出，请检查 .history/write.lock"
        ) from exc
    os.close(descriptor)
    try:
        validate_recorded_versions(directory, pages)
        history = _history(directory)
        current = _entries(pages)
        snapshot = {
            "schema_version": "1.0",
            "files": {
                name: d.model_dump(mode="json", exclude_unset=True) for name, d in pages.sources
            },
            "effective": [effective_configuration(p) for p in pages.profiles],
        }
        snapshot_id = digest(snapshot)
        destination = root / f"{snapshot_id}.json"
        payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if destination.exists():
            if destination.read_text(encoding="utf-8") != payload:
                raise ValueError("本地配置快照内容校验失败")
        else:
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        updated = VersionHistory(versions={**history.versions, **current})
        temporary = root / f"versions.{uuid4().hex}.tmp"
        try:
            temporary.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, root / "versions.json")
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "snapshot_id": snapshot_id,
            "path": str(destination.resolve()),
            "versions": len(current),
        }
    finally:
        lock.unlink()


def record_snapshot_files(files: dict[str, str], pages: "ConfiguredPages") -> dict:
    """Add immutable version history to an in-memory candidate."""
    validate_snapshot_versions(files, pages)
    history = (
        parse_history(files[".history/versions.json"])
        if ".history/versions.json" in files
        else VersionHistory()
    )
    entries = _entries(pages)
    snapshot = {
        "schema_version": "1.0",
        "files": {
            name: definition.model_dump(mode="json", exclude_unset=True)
            for name, definition in pages.sources
        },
        "effective": [effective_configuration(profile) for profile in pages.profiles],
    }
    snapshot_id = digest(snapshot)
    key = f".history/{snapshot_id}.json"
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if key in files and files[key] != payload:
        raise ValueError("配置快照内容校验失败")
    files[key] = payload
    files[".history/versions.json"] = (
        VersionHistory(versions={**history.versions, **entries}).model_dump_json(indent=2) + "\n"
    )
    return {"snapshot_id": snapshot_id, "versions": len(entries)}

"""Load a complete startup snapshot. Invalid files never partially take effect."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.profiles import BusinessProfile


@dataclass(frozen=True)
class ConfiguredPages:
    profiles: tuple[BusinessProfile, ...]
    active: tuple[BusinessProfile, ...]
    sources: tuple[tuple[str, PageDefinition], ...] = ()


def load_definitions(definitions, active) -> ConfiguredPages:
    """Compile relational aggregates directly; no filenames or implicit templates."""
    sources, profiles, selected = [], [], []
    identities = set()
    for data in definitions:
        definition = PageDefinition.model_validate(
            {**data, "active": active.get(data["page_id"]) == data["version"]}
        )
        identity = (definition.page_id, definition.version)
        if identity in identities or definition.schema_version != "2.0" or definition.extends:
            raise ValueError("数据库要求唯一、独立的完整页面版本")
        identities.add(identity)
        profile = compile_page(definition)
        sources.append((f"{definition.page_id}@{definition.version}", definition))
        profiles.append(profile)
        if definition.active:
            selected.append(profile)
    if not active or {(p.page_id, p.version) for p in selected} != set(active.items()):
        raise ValueError("数据库缺少明确有效的发布页面选择")
    return ConfiguredPages(tuple(profiles), tuple(selected), tuple(sources))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 属性重复：{key}")
        result[key] = value
    return result


def load_page_profiles(directory: Path) -> ConfiguredPages:
    if not directory.is_dir():
        raise ValueError(f"页面配置目录不存在：{directory}")
    return load_snapshot_profiles(
        {
            path.name: path.read_text(encoding="utf-8-sig")
            for path in sorted(directory.glob("*.json"))
        }
    )


def load_snapshot_profiles(
    files: Mapping[str, str],
) -> ConfiguredPages:
    """Compile one immutable snapshot without a filesystem dependency."""
    definitions, active = [], {}
    for filename, text in sorted(files.items()):
        if "/" in filename or not filename.endswith(".json"):
            continue
        try:
            definition = PageDefinition.model_validate(
                json.loads(text.lstrip("\ufeff"), object_pairs_hook=_object)
            )
        except ValueError as exc:
            raise ValueError(f"页面配置 {filename} 无效：{exc}") from exc
        definitions.append(definition.model_dump(mode="json"))
        if definition.active:
            if definition.page_id in active:
                raise ValueError("同一 page_id 只能有一个 active=true 的配置版本")
            active[definition.page_id] = definition.version
    return load_definitions(definitions, active)

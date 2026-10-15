"""Load a complete startup snapshot. Invalid files never partially take effect."""

import json
from dataclasses import dataclass
from pathlib import Path

from forj_review_assistant.businesses.configuration.compiler import compile_page
from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.profiles import BusinessProfile


@dataclass(frozen=True)
class ConfiguredPages:
    profiles: tuple[BusinessProfile, ...]
    active: tuple[BusinessProfile, ...]
    sources: tuple[tuple[str, PageDefinition], ...] = ()


def profile_id(profile: BusinessProfile) -> str:
    return profile.page_id or f"{profile.business_type}:{profile.region}"


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 属性重复：{key}")
        result[key] = value
    return result


def load_page_profiles(
    directory: Path,
    templates: tuple[BusinessProfile, ...] = (),
) -> ConfiguredPages:
    if not directory.is_dir():
        raise ValueError(f"页面配置目录不存在：{directory}")
    available = {(profile_id(p), p.version): p for p in templates}
    active = {profile_id(p): p for p in templates}
    pending = {}
    sources = []
    for path in sorted(directory.glob("*.json")):
        try:
            definition = PageDefinition.model_validate(
                json.loads(
                    path.read_text(encoding="utf-8-sig"),
                    object_pairs_hook=_object,
                )
            )
        except ValueError as exc:
            raise ValueError(f"页面配置 {path.name} 无效：{exc}") from exc
        key = (definition.page_id, definition.version)
        if key in pending or key in available:
            raise ValueError(f"页面配置 {path.name} 重复定义了 {key}；修改配置请使用新版本")
        pending[key] = (path.name, definition)
        sources.append((path.name, definition))
    active_ids = [definition.page_id for _, definition in pending.values() if definition.active]
    if len(active_ids) != len(set(active_ids)):
        raise ValueError("同一 page_id 只能有一个 active=true 的配置版本")
    compiled = []
    while pending:
        ready = [
            key
            for key, (_, definition) in pending.items()
            if not definition.extends
            or (definition.extends.page_id, definition.extends.version) in available
        ]
        if not ready:
            raise ValueError(
                f"extends 引用了不存在的模板或形成循环：{', '.join(name for name, _ in pending.values())}"
            )
        for key in ready:
            filename, definition = pending.pop(key)
            template = (
                available.get((definition.extends.page_id, definition.extends.version))
                if definition.extends
                else None
            )
            try:
                profile = compile_page(definition, template)
            except ValueError as exc:
                raise ValueError(f"页面配置 {filename} 无效：{exc}") from exc
            available[key] = profile
            compiled.append(profile)
            if definition.active:
                active[definition.page_id] = profile
    result = ConfiguredPages(tuple(compiled), tuple(active.values()), tuple(sources))
    from forj_review_assistant.businesses.configuration.history import validate_recorded_versions

    validate_recorded_versions(directory, result)
    return result

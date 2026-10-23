"""Structured configuration state, independent of files and database adapters."""

from dataclasses import dataclass, field, replace

from forj_review_service.businesses.page_config.inspection import digest
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.contracts.errors import ConfigurationInputError


@dataclass(frozen=True)
class ConfigurationState:
    definitions: tuple[dict, ...]
    active: dict[str, str]
    revision: int = 0
    release_id: int | None = None
    receipts: dict[str, dict] = field(default_factory=dict)
    pending: dict | None = None

    def __post_init__(self):
        normalized = tuple(
            sorted(
                (
                    PageDefinition.model_validate(d).model_dump(mode="json", exclude={"active"})
                    for d in self.definitions
                ),
                key=lambda d: (d["page_id"], d["version"]),
            )
        )
        object.__setattr__(self, "definitions", normalized)
        object.__setattr__(self, "active", dict(self.active))

    def baseline(self, kernel):
        return digest(
            {
                "kernel": kernel,
                "revision": self.revision,
                "release_id": self.release_id,
                "active": self.active,
                "definitions": self.definitions,
            }
        )

    def page_baseline(self, page_id, kernel):
        version = self.active.get(page_id)
        if version is None:
            raise ConfigurationInputError("指定页面不存在活动版本")
        return digest(
            {"page_id": page_id, "source": self.source(page_id, version), "kernel": kernel}
        )

    def source(self, page_id, version):
        return next(
            (d for d in self.definitions if (d["page_id"], d["version"]) == (page_id, version)),
            None,
        )

    def stage(self, candidate, *, activation_only=False):
        from forj_review_service.businesses.page_config.maintenance import (
            validate_configurable_change,
        )

        parsed = PageDefinition.model_validate(candidate)
        if parsed.schema_version != "2.0" or parsed.extends or not parsed.active:
            raise ConfigurationInputError("正式配置必须是启用的独立完整 2.0 页面")
        if parsed.page_id not in self.active:
            raise ConfigurationInputError("字段维护只能修改已接入页面")
        content = parsed.model_dump(mode="json", exclude={"active"})
        previous = self.source(parsed.page_id, parsed.version)
        if activation_only:
            if previous != content:
                raise ConfigurationInputError("回退只能选择内容完全一致的历史版本")
        else:
            if previous is not None:
                raise ConfigurationInputError("新配置必须使用未存在的新版本号")
            baseline = self.source(parsed.page_id, self.active[parsed.page_id])
            validate_configurable_change({**baseline, "active": True}, candidate)
        return parsed, replace(
            self,
            definitions=self.definitions if previous else (*self.definitions, content),
            active={**self.active, parsed.page_id: parsed.version},
        )

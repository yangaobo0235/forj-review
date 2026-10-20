"""Filesystem-independent configuration validation, replay and version publication."""

import asyncio
import json
from pathlib import Path

import httpx

from forj_review_config_service.contracts.models import ConfigurationError, SessionRecord
from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_catalog import page_catalog
from forj_review_service.businesses.page_config.files import (
    KERNEL_VERSION,
)
from forj_review_service.businesses.page_config.history import record_snapshot_files
from forj_review_service.businesses.page_config.inspection import (
    compare_configurations,
    configuration_snapshot,
    digest,
    effective_configuration,
    profile_fingerprint,
)
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.rules.catalog import RULE_DEFINITIONS
from forj_review_service.runtime.configuration.service import (
    ReplaySuite,
    decision_policy,
    replay_suite,
    validated_snapshot,
)


class SnapshotKernel:
    """Configuration operations over one isolated in-memory snapshot."""

    def __init__(
        self,
        files: dict[str, str],
        cases_dir: Path,
        review_url: str,
        local_cases_dir: Path | None = None,
        *,
        configuration_source: str | None = None,
    ):
        self.files = dict(files)
        self.cases_dir, self.review_url = cases_dir, review_url
        self.local_cases_dir = local_cases_dir or cases_dir
        self.configuration_source = configuration_source

    def _configuration(self):
        return validated_snapshot(self.files)

    def _inspect(self):
        _, registry = self._configuration()
        return configuration_snapshot(registry.profiles, registry.active_profiles)

    def baseline(self) -> str:
        files = {
            name: text
            for name, text in sorted(self.files.items())
            if name.endswith(".json") and ("/" not in name or name.startswith(".history/"))
        }
        return digest({"kernel": KERNEL_VERSION, "snapshot": self._inspect(), "files": files})

    def context(self, page_id: str | None = None) -> dict:
        pages, registry = self._configuration()
        profiles = registry.active_profiles
        if not page_id:
            return {
                "templates": [
                    {
                        "page_id": p.page_id,
                        "version": p.version,
                        "business_type": str(p.business_type),
                        "region": str(p.region),
                        "versions": [
                            item.version for item in registry.profiles if item.page_id == p.page_id
                        ],
                        "title": p.pack_declaration.title if p.pack_declaration else p.page_id,
                    }
                    for p in profiles
                    if p.pack_declaration
                ],
                "case_sets": self.case_sets(),
            }
        profile = next((p for p in profiles if p.page_id == page_id), None)
        if profile is None:
            raise ConfigurationError("NOT_FOUND", "指定页面不存在", 404)
        if not profile.pack_declaration:
            raise ConfigurationError(
                "UNSUPPORTED_CAPABILITY", "此旧业务尚无声明式配置，请通过新页面配置接入", 422
            )
        source = next(
            (
                d.model_dump(mode="json", exclude_unset=True)
                for _, d in pages.sources
                if d.page_id == page_id and d.version == profile.version
            ),
            None,
        )
        return {
            "source": source,
            "effective": effective_configuration(profile),
            "reference": {"page_id": profile.page_id, "version": profile.version},
            "manifest": build_collect_manifest(profile.pack_declaration),
        }

    def capabilities(self) -> dict:
        return {
            "schema": PageDefinition.model_json_schema(),
            "kernel_version": KERNEL_VERSION,
            "decision_policy": decision_policy(),
            "rules": {
                key: {"fields": sorted(value.fields), "checks": list(value.checks)}
                for key, value in RULE_DEFINITIONS.items()
            },
        }

    def rollback_draft(self, page_id: str, version: str) -> dict:
        pages, registry = self._configuration()
        target = next(
            (p for p in registry.profiles if p.page_id == page_id and p.version == version), None
        )
        if not target:
            raise ConfigurationError("NOT_FOUND", "历史版本不存在", 404)
        definition = next(
            (d for _, d in pages.sources if d.page_id == page_id and d.version == version), None
        )
        if definition:
            return {**definition.model_dump(mode="json", exclude_unset=True), "active": True}
        return {
            "page_id": page_id,
            "version": version,
            "business_type": str(target.business_type),
            "region": str(target.region),
            "title": target.pack_declaration.title,
            "paths": ["/"],
            "active": True,
        }

    def template_members(self, reference: dict | None) -> dict:
        if not reference:
            return {}
        _, registry = self._configuration()
        target = next(
            (
                p
                for p in registry.profiles
                if p.page_id == reference.get("page_id") and p.version == reference.get("version")
            ),
            None,
        )
        if not target or not target.pack_declaration:
            return {}
        return {
            "fields": [f.key for f in target.pack_declaration.fields],
            "materials": [m.document_type for m in target.pack_declaration.materials],
        }

    def case_sets(self):
        return list(self._case_registry())

    def _case_registry(self):
        registry = {
            p.relative_to(self.cases_dir).with_suffix("").as_posix(): p
            for p in sorted(self.cases_dir.rglob("*.json"))
            if '"cases"' in p.read_text(encoding="utf-8-sig")
        }
        if self.local_cases_dir != self.cases_dir:
            registry.update(
                {"local/" + p.stem: p for p in sorted(self.local_cases_dir.glob("*.json"))}
            )
        return registry

    def _case_path(self, case_set_id: str) -> Path:
        if case_set_id not in self.case_sets():
            raise ConfigurationError("NOT_FOUND", "回放集不存在；只能选择已登记的独立验收样例", 404)
        return self._case_registry()[case_set_id]

    def register_cases(self, case_set_id: str, suite: dict):
        # This entry is for human-supplied test expectations, never a model tool.
        if not case_set_id or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in case_set_id
        ):
            raise ConfigurationError("INVALID_CASE_ID", "样例 ID 只允许小写字母、数字、下划线和短横线", 422)
        parsed = ReplaySuite.model_validate(suite)
        target = self.local_cases_dir / f"{case_set_id}.json"
        if target.exists():
            raise ConfigurationError("CASE_IMMUTABLE", "已登记基准不能覆盖，请登记新的样例集")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(parsed.model_dump_json(indent=2))
        return {
            "case_set_id": ("local/" if self.local_cases_dir != self.cases_dir else "")
            + case_set_id
        }

    def stage(self, record: SessionRecord) -> tuple[PageDefinition, dict[str, str]]:
        definition = PageDefinition.model_validate(record.draft)
        if not definition.active:
            raise ValueError("候选必须明确启用目标新版本")
        pages, registry = self._configuration()
        files = dict(self.files)
        if record.activation_only:
            if self.rollback_draft(definition.page_id, definition.version) != record.draft:
                raise ValueError("回退目标内容与历史版本不一致")
        elif any(
            p.page_id == definition.page_id and p.version == definition.version
            for p in registry.profiles
        ):
            raise ValueError("新配置必须使用未存在的新版本号")
        for filename, existing in pages.sources:
            if existing.page_id == definition.page_id:
                payload = existing.model_dump(mode="json", exclude_unset=True)
                payload["active"] = (
                    record.activation_only and existing.version == definition.version
                )
                files[filename] = json.dumps(payload, ensure_ascii=False)
        if not record.activation_only:
            files[digest([definition.page_id, definition.version]) + ".json"] = json.dumps(
                record.draft, ensure_ascii=False, indent=2
            )
        return definition, files

    def validate(self, record: SessionRecord) -> dict:
        if self.baseline() != record.baseline_digest:
            raise ConfigurationError("BASELINE_CHANGED", "配置或模板已改变，请重新建立编辑基线")
        definition, files = self.stage(record)
        _, registry = validated_snapshot(files)
        after = configuration_snapshot(registry.profiles, registry.active_profiles)
        profile = next(
            p
            for p in registry.profiles
            if p.page_id == definition.page_id and p.version == definition.version
        )
        return {
            "candidate_digest": digest(
                {
                    "draft": record.draft,
                    "baseline": record.baseline_digest,
                    "effective": profile_fingerprint(profile),
                }
            ),
            "effective_digest": profile_fingerprint(profile),
            "kernel_version": KERNEL_VERSION,
            "page_catalog": page_catalog(registry.active_profiles),
            "page_id": definition.page_id,
            "required_materials": sorted(
                {m.document_type for m in profile.pack_declaration.material_policy.materials}
            )
            if profile.pack_declaration.material_policy
            and profile.pack_declaration.material_policy.mode == "enforce"
            else [],
            "diff": compare_configurations(self._inspect(), after),
            "manifest": build_collect_manifest(profile.pack_declaration),
        }

    def _prepare_replay(self, record: SessionRecord, case_set_id: str):
        preview = self.validate(record)
        definition, files = self.stage(record)
        suite = ReplaySuite.model_validate_json(
            self._case_path(case_set_id).read_text(encoding="utf-8-sig")
        )
        if not any(
            c.request.page_id == definition.page_id
            and c.request.profile_version == definition.version
            for c in suite.cases
        ):
            raise ConfigurationError("MISSING_REPLAY", "样例必须包含目标页面和候选版本")
        _, registry = validated_snapshot(files)
        return preview, registry, suite

    async def replay(self, record: SessionRecord, case_set_id: str) -> dict:
        preview, registry, suite = await asyncio.to_thread(
            self._prepare_replay, record, case_set_id
        )
        result = await replay_suite(registry, suite)
        result.update(
            case_set_id=case_set_id,
            case_digest=digest(suite.model_dump(mode="json")),
            revision=record.revision,
            candidate_digest=preview["candidate_digest"],
        )
        return result

    def verify_replay_receipt(self, result: dict) -> bool:
        try:
            suite = ReplaySuite.model_validate_json(
                self._case_path(result["case_set_id"]).read_text(encoding="utf-8-sig")
            )
            return digest(suite.model_dump(mode="json")) == result["case_digest"]
        except (ConfigurationError, ValueError, OSError):
            return False

    def commit(self, record: SessionRecord, operation_id: str) -> dict:
        previous = self.commit_receipt(operation_id)
        if previous is not None:
            if (previous["page_id"], previous["version"], previous["candidate_digest"]) != (
                record.draft["page_id"],
                record.draft["version"],
                record.report["candidate_digest"],
            ):
                raise ConfigurationError("IDEMPOTENCY_CONFLICT", "保存操作 ID 已用于其他候选")
            return previous
        validated = self.validate(record)
        if validated["candidate_digest"] != record.report["candidate_digest"]:
            raise ConfigurationError("STALE_REVISION", "验证后的候选已改变")
        _, files = self.stage(record)
        pages, _ = validated_snapshot(files)
        record_snapshot_files(files, pages)
        receipt = {
            "operation_id": operation_id,
            "page_id": record.draft["page_id"],
            "version": record.draft["version"],
            "candidate_digest": validated["candidate_digest"],
            "effective_digest": validated["effective_digest"],
            "kernel_version": KERNEL_VERSION,
            "state": "SAVED_PENDING_RESTART",
        }
        files[self._receipt_key(operation_id)] = json.dumps(receipt, ensure_ascii=False)
        self.files = files
        return receipt

    @staticmethod
    def _receipt_key(operation_id: str) -> str:
        if not operation_id or not operation_id.isalnum():
            raise ValueError("保存操作标识无效")
        return ".receipts/" + operation_id + ".json"

    def commit_receipt(self, operation_id: str) -> dict | None:
        text = self.files.get(self._receipt_key(operation_id))
        return json.loads(text) if text is not None else None

    async def activation(self, record: SessionRecord) -> dict:
        if not record.receipt:
            return {"state": "DRAFTING"}
        try:
            async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(self.review_url + "/api/review/configuration/loaded")
                response.raise_for_status()
                loaded = response.json()
            match = next(
                (p for p in loaded["pages"] if p["page_id"] == record.receipt["page_id"]), None
            )
            effective = (
                match is not None
                and match["version"] == record.receipt["version"]
                and match["fingerprint"] == record.receipt["effective_digest"]
                and loaded["kernel_version"] == KERNEL_VERSION
                and (
                    loaded.get("configuration_source") == self.configuration_source
                    if self.configuration_source
                    else False
                )
            )
            return {
                "state": "EFFECTIVE" if effective else "SAVED_PENDING_RESTART",
                "loaded": loaded,
                "reason": "已核对加载版本" if effective else "审核服务加载目录、版本或摘要不一致",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return {
                "state": "SAVED_PENDING_RESTART",
                "reason": "等待审核服务启动并核对实际加载版本",
            }

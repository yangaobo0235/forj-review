"""Filesystem-independent configuration validation, replay and version publication."""

import asyncio
from pathlib import Path

import httpx
from pydantic import ValidationError

from forj_review_config_service.contracts.models import ConfigurationError, TaskRecord
from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_config.inspection import (
    digest,
    effective_configuration,
)
from forj_review_service.businesses.page_config.kernel import (
    KERNEL_VERSION,
)
from forj_review_service.businesses.page_config.maintenance import (
    edit_fields,
    field_dependencies,
    maintenance_contract,
)
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.page_config.state import ConfigurationState
from forj_review_service.businesses.rules.catalog import RULE_DEFINITIONS
from forj_review_service.contracts.errors import ConfigurationInputError
from forj_review_service.runtime.configuration.service import (
    ReplaySuite,
    decision_policy,
    replay_suite,
    validated_snapshot,
)
from forj_review_service.runtime.configuration.verification import (
    evaluate_collection,
    proof_is_current,
)


class SnapshotKernel:
    """Configuration operations over one isolated in-memory snapshot."""

    @staticmethod
    def edit_fields(draft, operations):
        try:
            return edit_fields(draft, operations)
        except ConfigurationInputError as exc:
            raise ConfigurationError("FIELD_MAINTENANCE_ONLY", str(exc), 422) from exc
        except ValidationError as exc:
            raise ConfigurationError(
                "INVALID_CONFIGURATION",
                "配置属性不符合契约："
                + "; ".join(
                    ".".join(map(str, error["loc"])) + ":" + error["type"]
                    for error in exc.errors()[:10]
                ),
                422,
            ) from exc

    evaluate_collection = staticmethod(evaluate_collection)
    proof_is_current = staticmethod(proof_is_current)

    @staticmethod
    def page_options(draft):
        from forj_review_service.businesses.page_config.row_contract import split_definition

        return split_definition(draft)

    @staticmethod
    def field_dependencies(draft):
        return {
            item["key"]: field_dependencies(draft, item["key"]) for item in draft.get("fields", [])
        }

    @staticmethod
    def comparison_configurable_fields(draft):
        return [
            key
            for key, field in maintenance_contract(draft)["fields"].items()
            if field["comparison_configurable"]
        ]

    def __init__(
        self,
        configuration: ConfigurationState,
        cases_dir: Path,
        review_url: str,
        local_cases_dir: Path | None = None,
        *,
        configuration_source: str | None = None,
    ):
        self.configuration = configuration
        self.cases_dir, self.review_url = cases_dir, review_url
        self.local_cases_dir = local_cases_dir or cases_dir
        self.configuration_source = configuration_source

    def _configuration(self):
        return validated_snapshot(self.configuration)

    def baseline(self, page_id: str | None = None) -> str:
        return (
            self.configuration.page_baseline(page_id, KERNEL_VERSION)
            if page_id
            else self.configuration.baseline(KERNEL_VERSION)
        )

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
        editable = source
        return {
            "source": source,
            "editable": editable,
            "maintenance": maintenance_contract(editable),
            "dependencies": {
                field.key: field_dependencies(editable, field.key)
                for field in profile.pack_declaration.fields
            },
            "effective": effective_configuration(profile),
            "reference": {"page_id": profile.page_id, "version": profile.version},
            "manifest": build_collect_manifest(profile.pack_declaration),
        }

    def capabilities(self) -> dict:
        from forj_review_service.businesses.page_config.row_contract import (
            ActionBinding,
            RuleBinding,
        )

        return {
            "maintenance": maintenance_contract(),
            "rule_binding_schema": RuleBinding.model_json_schema(),
            "action_binding_schema": ActionBinding.model_json_schema(),
            "schema": PageDefinition.model_json_schema(),
            "kernel_version": KERNEL_VERSION,
            "operations": {
                "field": [
                    "upsert_field",
                    "remove_field",
                    "set_collection_binding",
                    "set_field_comparison",
                ],
                "material": ["upsert_material", "remove_material", "set_material_policy"],
                "policy": ["set_policy_dates"],
                "presentation": ["set_presentation"],
                "sensitive": ["set_field_permissions"],
            },
            "limits": {"new_pages": False, "code_execution": False, "rule_parameters": False},
            "execution": {
                "mode": "CONFIGURATION_SERVICE",
                "code_tools": False,
                "local_entrypoint": "forj-review-config-develop",
                "notice": "本机网页仍是配置服务；开发者显式启动本地入口后才有代码工具。code_execution不表示配置表达能力。",
            },
            "decision_policy": decision_policy(),
            "rules": {
                key: {"fields": sorted(value.fields), "checks": list(value.checks)}
                for key, value in RULE_DEFINITIONS.items()
            },
        }

    def rollback_draft(self, page_id: str, version: str) -> dict:
        from forj_review_service.runtime.configuration.publication import rollback_definition

        try:
            return rollback_definition(self.configuration, page_id, version)
        except ConfigurationInputError as exc:
            raise ConfigurationError("NOT_FOUND", "历史版本不存在", 404) from exc

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
            raise ConfigurationError(
                "INVALID_CASE_ID", "样例 ID 只允许小写字母、数字、下划线和短横线", 422
            )
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

    def read_cases(self, case_set_id: str):
        return ReplaySuite.model_validate_json(
            self._case_path(case_set_id).read_text(encoding="utf-8-sig")
        ).model_dump(mode="json")

    def stage(self, record: TaskRecord) -> tuple[PageDefinition, ConfigurationState]:
        from forj_review_service.runtime.configuration.publication import stage_version

        try:
            return stage_version(
                self.configuration, record.draft, activation_only=record.activation_only
            )
        except ConfigurationInputError as exc:
            raise ConfigurationError("INVALID_CONFIGURATION", str(exc), 422) from exc
        except ValidationError as exc:
            raise ConfigurationError("INVALID_CONFIGURATION", "候选配置格式无效", 422) from exc

    def validate(self, record: TaskRecord) -> dict:
        _, registry = self._configuration()
        if record.draft["page_id"] not in {p.page_id for p in registry.active_profiles}:
            raise ConfigurationError("INVALID_CONFIGURATION", "只能维护已接入页面的活动配置", 422)
        if self.baseline(record.draft["page_id"]) != record.baseline_digest:
            raise ConfigurationError("BASELINE_CHANGED", "配置或模板已改变，请重新建立编辑基线")
        from forj_review_service.runtime.configuration.publication import preview_version

        try:
            return preview_version(
                self.configuration, record.draft, activation_only=record.activation_only
            )
        except ConfigurationInputError as exc:
            raise ConfigurationError("INVALID_CONFIGURATION", str(exc), 422) from exc
        except ValidationError as exc:
            raise ConfigurationError("INVALID_CONFIGURATION", "候选配置格式无效", 422) from exc

    def _prepare_replay(self, record: TaskRecord, case_set_id: str):
        preview = self.validate(record)
        definition, files = self.stage(record)
        suite = ReplaySuite.model_validate_json(
            self._case_path(case_set_id).read_text(encoding="utf-8-sig")
        )
        source_digest = digest(suite.model_dump(mode="json"))
        cases = [
            c.model_copy(deep=True) for c in suite.cases if c.request.page_id == definition.page_id
        ]
        if not cases:
            raise ConfigurationError("MISSING_REPLAY", "样例必须包含目标页面")
        for case in cases:
            # Reuse trusted inputs and expectations. Only the execution target is rebound.
            case.request.profile_version = definition.version
        suite = suite.model_copy(update={"cases": tuple(cases)})
        _, registry = validated_snapshot(files)
        return preview, registry, suite, source_digest

    async def replay(self, record: TaskRecord, case_set_id: str) -> dict:
        preview, registry, suite, source_digest = await asyncio.to_thread(
            self._prepare_replay, record, case_set_id
        )
        result = await replay_suite(registry, suite)
        result.update(
            case_set_id=case_set_id,
            case_digest=source_digest,
            execution_digest=digest(suite.model_dump(mode="json")),
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

    def commit(self, record: TaskRecord, operation_id: str) -> dict:
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
        from forj_review_service.runtime.configuration.publication import commit_version

        self.configuration, receipt = commit_version(
            self.configuration,
            record.draft,
            operation_id,
            validated["candidate_digest"],
            activation_only=record.activation_only,
        )
        return receipt

    def commit_receipt(self, operation_id: str) -> dict | None:
        if not operation_id or not operation_id.isalnum():
            raise ConfigurationError("INVALID_OPERATION_ID", "保存操作标识无效", 422)
        return self.configuration.receipts.get(operation_id)

    async def activation(self, record: TaskRecord) -> dict:
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
                    not record.receipt.get("release_id")
                    or loaded.get("release_id") == record.receipt["release_id"]
                )
                and (
                    loaded.get("configuration_source") == self.configuration_source
                    if self.configuration_source
                    else False
                )
            )
            return {
                "state": (
                    "EFFECTIVE"
                    if effective
                    else "LOAD_FAILED"
                    if loaded.get("load_error")
                    else "SUPERSEDED"
                    if loaded.get("release_id") != record.receipt.get("release_id")
                    and loaded.get("desired_release_id") == loaded.get("release_id")
                    else "LOADING"
                ),
                "loaded": loaded,
                "reason": "已核对加载版本"
                if effective
                else "审核服务未加载该发布，请查看目标发布与加载错误",
            }
        except (httpx.HTTPError, ValueError, KeyError):
            return {
                "state": "UNAVAILABLE",
                "reason": "等待审核服务启动并核对实际加载版本",
            }

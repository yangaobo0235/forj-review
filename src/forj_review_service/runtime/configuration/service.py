"""Local configuration inspection and deterministic, network-free replay."""

from pathlib import Path
from typing import Literal

from pydantic import Field

from forj_review_service.businesses.packs import build_collect_manifest
from forj_review_service.businesses.page_config.inspection import (
    configuration_snapshot,
    profile_fingerprint,
)
from forj_review_service.businesses.page_config.schema import Declaration, Key
from forj_review_service.businesses.profiles import CONSISTENCY_DEFAULT
from forj_review_service.businesses.registry import BusinessRegistry
from forj_review_service.contracts.decisions import DECISION_POLICY, ReasonCode
from forj_review_service.contracts.engine_emissions import (
    EngineEmissionLookupUnavailable,
    engine_model_key,
)
from forj_review_service.contracts.evidence import EvidenceFact
from forj_review_service.contracts.recognition import CheckResult, RecognitionBatchResult
from forj_review_service.contracts.review import QrCheck, ReviewRequest
from forj_review_service.runtime.review_service import ReviewService


def decision_policy() -> dict:
    """提供给配置端的审核语义能力，不暴露或修改内核实现。"""
    return dict(DECISION_POLICY)


def validated_configuration(directory: Path):
    from forj_review_service.businesses.page_config.loader import load_page_profiles
    from forj_review_service.businesses.page_config.state import ConfigurationState

    pages = load_page_profiles(directory)
    state = ConfigurationState(
        tuple(d.model_dump(mode="json") for _, d in pages.sources),
        {p.page_id: p.version for p in pages.active},
    )
    return validated_snapshot(state)


def validated_snapshot(state):
    from dataclasses import replace

    from forj_review_service.businesses.page_config.loader import load_definitions

    templates = (CONSISTENCY_DEFAULT,)
    pages = load_definitions(state.definitions, state.active)
    pages = replace(pages, active=(*templates, *pages.active))
    return validate_pages(pages, templates)


def validate_pages(pages, templates):
    registry = BusinessRegistry((*templates, *pages.profiles), active_profiles=pages.active)
    ReviewService(registry=registry, load_model=False)  # Offline: no model credentials loaded.
    return pages, registry


def inspect_directory(directory: Path) -> dict:
    _, registry = validated_configuration(directory)
    return configuration_snapshot(registry.profiles, registry.active_profiles)


class ExternalFixture(Declaration):
    checks: tuple[CheckResult, ...] = ()
    qr_checks: tuple[QrCheck, ...] = ()
    evidence: tuple[EvidenceFact, ...] = ()


class ReplayExpect(Declaration):
    recommendation: Literal["PASS", "REVIEW_REQUIRED"]
    comparisons: dict[Key, Literal["MATCH", "CONFLICT", "REVIEW_REQUIRED"]] = Field(
        default_factory=dict
    )
    checks: dict[str, Literal["MATCH", "CONFLICT", "INSUFFICIENT"]] = Field(default_factory=dict)
    comparison_reasons: dict[Key, list[ReasonCode]] = Field(default_factory=dict)
    check_reasons: dict[str, list[ReasonCode]] = Field(default_factory=dict)
    capabilities: dict[
        str,
        Literal[
            "READY",
            "SKIPPED",
            "BLOCKED",
            "NOT_CONFIGURED",
            "SUCCEEDED",
            "FAILED",
        ],
    ] = Field(default_factory=dict)
    comparison_fields: tuple[Key, ...] | None = None
    manifest_fields: tuple[Key, ...] | None = None
    task_fields: tuple[Key, ...] | None = None
    writable_fields: tuple[Key, ...] | None = None


class ReplayCase(Declaration):
    name: str
    request: ReviewRequest
    batch: RecognitionBatchResult  # Required: a replay must never fall through to live recognition.
    expect: ReplayExpect
    external_results: dict[str, ExternalFixture] = Field(default_factory=dict)
    engine_emissions: dict[str, Literal["国三", "国四", "国五", "国六"]] | None = None


class ReplaySuite(Declaration):
    schema_version: Literal["1.0"] = "1.0"
    cases: tuple[ReplayCase, ...] = Field(min_length=1)


class _ReplayService(ReviewService):
    def __init__(self, *, engine_emissions, **kwargs):
        super().__init__(**kwargs)
        self._engine_emissions = (
            {engine_model_key(key): value for key, value in engine_emissions.items()}
            if engine_emissions is not None
            else None
        )

    async def lookup_engine_emissions(self, models):
        if self._engine_emissions is None:
            raise EngineEmissionLookupUnavailable("离线回放未提供发动机排放参考数据")
        return {
            model: self._engine_emissions[model]
            for model in models
            if model in self._engine_emissions
        }

    async def _extract_documents(self, *args, **kwargs):
        raise RuntimeError("离线回放禁止调用模型识别")


async def replay_cases(directory: Path, cases_path: Path) -> dict:
    _, registry = validated_configuration(directory)
    suite = ReplaySuite.model_validate_json(cases_path.read_text(encoding="utf-8-sig"))
    return await replay_suite(registry, suite)


async def replay_suite(registry: BusinessRegistry, suite: ReplaySuite) -> dict:
    if len({case.name for case in suite.cases}) != len(suite.cases):
        raise ValueError("回放样例名称重复")
    external_ids = {
        spec.capability_id
        for profile in registry.profiles
        for spec in profile.capabilities
        if spec.kind == "EXTERNAL"
    }
    results = []
    for case in suite.cases:
        if not case.request.page_id:
            raise ValueError(f"回放样例 {case.name} 必须指定 page_id 和精确配置版本")
        unknown = case.external_results.keys() - external_ids
        if unknown:
            raise ValueError(
                f"回放样例 {case.name} 引用了不存在的外部能力：{', '.join(sorted(unknown))}"
            )

        async def external_stub(context, spec, fixtures=case.external_results):
            fixture = fixtures.get(spec.capability_id)
            if fixture is None:
                return (
                    CheckResult(
                        check_id=f"EXTERNAL-{spec.capability_id}",
                        label="离线外部核验",
                        status="INSUFFICIENT",
                        reason="离线回放未提供外部核验结果",
                    ),
                )
            return (*fixture.checks, *fixture.qr_checks, *fixture.evidence)

        service = _ReplayService(
            engine_emissions=case.engine_emissions,
            registry=registry,
            external_check_handlers={key: external_stub for key in external_ids},
            load_model=False,
        )
        profile = service.resolve_profile(case.request)
        try:
            response, _ = await service.assist_with_batch_async(
                case.request.model_copy(deep=True),
                initial_batch=case.batch.model_copy(deep=True),
            )
        finally:
            await service.aclose()
        actual = {
            "recommendation": response.recommendation.value,
            "comparisons": {c.field: c.status.value for c in response.comparisons},
            "comparison_reasons": {c.field: c.reason_codes for c in response.comparisons},
            "checks": {
                c.check_id: c.status
                for c in (
                    *(c for result in response.capability_results for c in result.checks),
                    *response.cross_checks,
                )
            },
            "capabilities": {r.capability_id: r.status for r in response.capability_results},
            "check_reasons": {
                c.check_id: c.reason_codes
                for c in (
                    *(c for result in response.capability_results for c in result.checks),
                    *response.cross_checks,
                )
            },
            "comparison_fields": sorted(c.field for c in response.comparisons),
            "manifest_fields": sorted(
                f["key"] for f in build_collect_manifest(profile.pack_declaration)["fields"]
            )
            if profile.pack_declaration
            else [],
            "task_fields": sorted(
                {
                    t.page_target_field or t.page_field
                    for t in response.review_tasks
                    if t.category == "FIELD" and (t.page_target_field or t.page_field)
                }
            ),
            "writable_fields": sorted(
                {
                    t.page_target_field or t.page_field
                    for t in response.review_tasks
                    if t.writable and (t.page_target_field or t.page_field)
                }
            ),
        }
        failures = []
        for key, expected in case.expect.model_dump(exclude_none=True).items():
            if isinstance(expected, dict):
                for field, status in expected.items():
                    if actual[key].get(field) != status:
                        failures.append(
                            {
                                "path": f"{key}.{field}",
                                "expected": status,
                                "actual": actual[key].get(field),
                            }
                        )
            elif key.endswith("_fields"):
                if sorted(expected) != actual[key]:
                    failures.append(
                        {"path": key, "expected": sorted(expected), "actual": actual[key]}
                    )
            elif expected != actual[key]:
                failures.append({"path": key, "expected": expected, "actual": actual[key]})
        results.append(
            {
                "name": case.name,
                "ok": not failures,
                "page_id": profile.page_id,
                "version": profile.version,
                "fingerprint": profile_fingerprint(profile),
                "failures": failures,
                "actual": actual,
            }
        )
    return {"ok": all(r["ok"] for r in results), "cases": results}

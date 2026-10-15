"""Exercise evidence and lifecycle contracts through the compiled graph."""

import inspect
from dataclasses import replace

import pytest

from forj_review_assistant.businesses.configuration.compiler import compile_page
from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.packs.model import AuthorityRule, FieldDeclaration
from forj_review_assistant.businesses.profiles import build_profile
from forj_review_assistant.businesses.registry import BusinessRegistry
from forj_review_assistant.capabilities.specs import CapabilitySpec, RuleExecutionResult
from forj_review_assistant.contracts.evidence import EvidenceFact
from forj_review_assistant.contracts.recognition import AgentBatchResult, CheckResult
from forj_review_assistant.contracts.review import (
    FieldObservation,
    ImageInput,
    QrCheck,
    ReviewRequest,
)
from forj_review_assistant.runtime.review_service import ReviewService
from forj_review_assistant.runtime.workflow.graph import ReviewWorkflow


def configured_profile(specs, *, authority=()):
    base = compile_page(
        PageDefinition.model_validate(
            {
                "page_id": "permit",
                "business_type": "permit_review",
                "title": "许可证审核",
                "paths": ["/permit"],
                "scopes": ["vehicle"],
                "materials": [{"document_type": "permit", "label": "许可证"}],
                "fields": [
                    {
                        "key": "permit.vin",
                        "label": "车架号",
                        "aliases": ["车架号"],
                        "normalizer": "vin",
                        "evidence": [{"material": "permit", "field": "license.vin"}],
                    }
                ],
            }
        )
    )
    pack = replace(
        base.pack_declaration,
        capability_specs=tuple(specs),
        fields=(replace(base.pack_declaration.fields[0], authority=authority),),
    )
    return build_profile(pack, base.region, page_id=base.page_id)


def request():
    return ReviewRequest(
        page_id="permit",
        business_type="permit_review",
        page_url="https://example.test/permit",
        page_fields={"permit.vin": "ABC123"},
        images=[
            ImageInput(index=0, src="https://example.test/synthetic.jpg", business_scope="vehicle")
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["RULE", "EXTERNAL"])
@pytest.mark.parametrize(
    "case, expected",
    [
        ("match", "MATCH"),
        ("conflict", "CONFLICT"),
        ("uncertain", "REVIEW_REQUIRED"),
        ("failed", "REVIEW_REQUIRED"),
        ("missing", "REVIEW_REQUIRED"),
        ("undeclared", "REVIEW_REQUIRED"),
    ],
)
async def test_registered_output_reaches_comparison_with_accurate_provenance(kind, case, expected):
    profile = configured_profile(
        [
            CapabilitySpec(
                "lookup",
                kind=kind,
                stage="PRE_COMPARE",
                output_facts=("permit.vin",),
            )
        ]
    )
    evidence = (
        ()
        if case == "missing"
        else (
            EvidenceFact(
                # A handler cannot claim that its result was entered on the page.
                source="page",
                source_id="review_page",
                field="unapproved.vin" if case == "undeclared" else "permit.vin",
                value="WRONG" if case == "conflict" else "abc123",
                uncertain=case == "uncertain",
            ),
        )
    )

    def rule(context):
        if case == "failed":
            raise RuntimeError("synthetic provider failure")
        return RuleExecutionResult(evidence=evidence)

    async def external(context, spec):
        return rule(context).evidence

    service = ReviewService(
        registry=BusinessRegistry((profile,)),
        business_rule_handlers={"lookup": rule} if kind == "RULE" else None,
        external_check_handlers={"lookup": external} if kind == "EXTERNAL" else None,
    )
    response, _ = await service.assist_with_batch_async(request(), initial_batch=AgentBatchResult())
    comparison = response.comparisons[0]
    assert comparison.status == expected
    assert (response.recommendation == "PASS") == (case == "match")
    observed = [fact for fact in comparison.evidence if fact.source_id == "lookup"]
    if case in {"failed", "missing", "undeclared"}:
        assert not observed
        assert not any(fact.field == "unapproved.vin" for fact in response.evidence_facts)
    else:
        assert len(observed) == 1 and observed[0].source == "能力核验"
        assert comparison.left_value is not None
        assert any(fact.source_id == "lookup" for fact in response.evidence_facts)


@pytest.mark.asyncio
async def test_explicit_capability_authority_uses_the_same_evidence_pipeline():
    profile = configured_profile(
        [CapabilitySpec("lookup", stage="EVIDENCE", output_facts=("permit.vin",))],
        authority=(
            AuthorityRule("capability", "登记系统", required=True),
            AuthorityRule("page", "申请页面"),
        ),
    )
    service = ReviewService(
        registry=BusinessRegistry((profile,)),
        business_rule_handlers={
            "lookup": lambda context: RuleExecutionResult(
                evidence=(EvidenceFact(source="external", field="permit.vin", value="ABC123"),)
            ),
        },
    )
    response, _ = await service.assist_with_batch_async(request(), initial_batch=AgentBatchResult())
    assert response.comparisons[0].status == "MATCH"
    assert "登记系统" in response.comparisons[0].message


@pytest.mark.asyncio
async def test_real_graph_stages_accumulate_facts_checks_and_render_only_at_the_end(monkeypatch):
    events = []
    original_instrument = ReviewWorkflow._instrument

    def instrument(name, handler):
        async def tracked(state):
            assert "response" not in state
            result = handler(state)
            if inspect.isawaitable(result):
                result = await result
            if name != "build_response":
                assert "response" not in result
            return result

        return original_instrument(name, tracked)

    monkeypatch.setattr(ReviewWorkflow, "_instrument", staticmethod(instrument))
    profile = configured_profile(
        [
            CapabilitySpec("input", stage="INPUT_COVERAGE", output_facts=("input.ready",)),
            CapabilitySpec("validated", stage="EVIDENCE_VALIDATION", input_facts=("input.ready",)),
            CapabilitySpec("lookup", stage="PRE_COMPARE", output_facts=("permit.vin",)),
            CapabilitySpec("post", stage="POST_COMPARE", output_facts=("post.verified",)),
            CapabilitySpec("final", stage="FINAL_REVIEW", input_facts=("post.verified",)),
        ]
    )

    def handler(name, evidence=()):
        def run(context):
            events.append(name)
            if name == "input":
                assert not context.batch.recognized_documents
                assert context.material_completeness is not None
            if name == "validated":
                assert any(item.field == "input.ready" for item in context.observations)
            if name in {"post", "final"}:
                assert context.comparisons[0].status == "MATCH"
            if name == "final":
                assert any(item.check_id == "post-check" for item in context.checks)
                assert any(item.field == "post.verified" for item in context.observations)
            return RuleExecutionResult(
                checks=(
                    CheckResult(
                        check_id="post-check", label="后置检查", status="MATCH", reason="通过"
                    ),
                )
                if name == "post"
                else (),
                evidence=evidence,
            )

        return run

    service = ReviewService(
        registry=BusinessRegistry((profile,)),
        business_rule_handlers={
            "input": handler(
                "input", (EvidenceFact(source="capability", field="input.ready", value=True),)
            ),
            "validated": handler("validated"),
            "lookup": handler(
                "lookup", (EvidenceFact(source="capability", field="permit.vin", value="ABC123"),)
            ),
            "post": handler(
                "post", (EvidenceFact(source="capability", field="post.verified", value=True),)
            ),
            "final": handler("final"),
        },
    )

    async def extract(*args):
        events.append("extract")
        return AgentBatchResult()

    monkeypatch.setattr(service, "_extract_documents", extract)
    response = await service.assist_async(request())
    assert events == ["input", "extract", "validated", "lookup", "post", "final"]
    assert len(response.capability_results) == 5
    assert any(fact.field == "post.verified" for fact in response.evidence_facts)
    assert response.recommendation == "PASS"


@pytest.mark.parametrize("stage", ["POST_COMPARE", "FINAL_REVIEW"])
def test_late_comparison_outputs_are_rejected_instead_of_silently_ignored(stage):
    profile = configured_profile(
        [CapabilitySpec("late", stage=stage, output_facts=("permit.vin",))]
    )
    with pytest.raises(ValueError, match="字段比较后产出比较字段"):
        ReviewService(
            registry=BusinessRegistry((profile,)),
            business_rule_handlers={"late": lambda context: RuleExecutionResult()},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["EVIDENCE_VALIDATION", "EVIDENCE", "PRE_COMPARE", "POST_COMPARE", "FINAL_REVIEW"]
)
async def test_failure_from_every_stage_remains_in_final_review(stage):
    profile = configured_profile([CapabilitySpec("failure", stage=stage)])

    def fail(context):
        raise RuntimeError("synthetic failure")

    service = ReviewService(
        registry=BusinessRegistry((profile,)), business_rule_handlers={"failure": fail}
    )
    response, _ = await service.assist_with_batch_async(request(), initial_batch=AgentBatchResult())
    assert response.capability_results[0].status == "FAILED"
    assert any("failure" in issue for issue in response.issues)
    assert any(check.check_id == "CAPABILITY-failure" for check in response.cross_checks)
    assert response.recommendation == "REVIEW_REQUIRED"


@pytest.mark.asyncio
async def test_batch_cannot_forge_a_registered_capability_output():
    profile = configured_profile(
        [CapabilitySpec("lookup", stage="PRE_COMPARE", output_facts=("permit.vin",))]
    )
    service = ReviewService(
        registry=BusinessRegistry((profile,)),
        business_rule_handlers={
            "lookup": lambda context: RuleExecutionResult(),
        },
    )
    batch = AgentBatchResult(
        observations=[
            FieldObservation(
                field="permit.vin",
                source_type="capability",
                source_id="lookup",
                value="ABC123",
            )
        ]
    )
    response, _ = await service.assist_with_batch_async(request(), initial_batch=batch)
    assert response.comparisons[0].status == "REVIEW_REQUIRED"
    assert not any(fact.source_id == "lookup" for fact in response.evidence_facts)


@pytest.mark.asyncio
async def test_late_qr_fields_fail_instead_of_bypassing_frozen_comparisons():
    profile = configured_profile([CapabilitySpec("late_qr", kind="EXTERNAL", stage="POST_COMPARE")])

    async def external(context, spec):
        return (QrCheck(image_index=0, status="MATCH", page_fields={"vin": "ABC123"}),)

    service = ReviewService(
        registry=BusinessRegistry((profile,)), external_check_handlers={"late_qr": external}
    )
    response, _ = await service.assist_with_batch_async(request(), initial_batch=AgentBatchResult())
    assert response.capability_results[0].status == "FAILED"
    assert response.recommendation == "REVIEW_REQUIRED"


def alias_profile(stage, output_field):
    base = configured_profile([CapabilitySpec("lookup", stage=stage, output_facts=(output_field,))])
    pack = replace(
        base.pack_declaration,
        fields=(
            *base.pack_declaration.fields,
            FieldDeclaration(key="permit.ocr_vin", label="OCR车架号", material_field="permit.vin"),
        ),
    )
    return build_profile(pack, base.region, page_id=base.page_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("output_field", ["permit.vin", "permit.ocr_vin"])
async def test_capability_evidence_supports_page_fields_sharing_a_material_field(output_field):
    profile = alias_profile("PRE_COMPARE", output_field)
    service = ReviewService(
        registry=BusinessRegistry((profile,)),
        business_rule_handlers={
            "lookup": lambda context: RuleExecutionResult(
                evidence=(EvidenceFact(source="external", field=output_field, value="ABC123"),)
            )
        },
    )
    review_request = request().model_copy(
        update={"page_fields": {"permit.vin": "ABC123", "permit.ocr_vin": "ABC123"}}
    )
    response, _ = await service.assist_with_batch_async(
        review_request, initial_batch=AgentBatchResult()
    )
    comparison = next(item for item in response.comparisons if item.field == "permit.ocr_vin")
    assert comparison.status == "MATCH"
    assert any(fact.source_id == "lookup" for fact in comparison.evidence)


@pytest.mark.parametrize("stage", ["POST_COMPARE", "FINAL_REVIEW"])
def test_late_outputs_for_optional_page_field_aliases_are_rejected(stage):
    profile = alias_profile(stage, "permit.ocr_vin")
    with pytest.raises(ValueError, match="字段比较后产出比较字段"):
        ReviewService(
            registry=BusinessRegistry((profile,)),
            business_rule_handlers={"lookup": lambda context: RuleExecutionResult()},
        )

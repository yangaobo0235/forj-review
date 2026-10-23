"""审核复盘回归：证据、总建议、二维码和任务取消的不变量。"""

import asyncio
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from forj_review_service.businesses.context import BusinessContext
from forj_review_service.businesses.field_policies import AuthorityRule, date_window_problem
from forj_review_service.businesses.page_config.compiler import compile_page
from forj_review_service.businesses.page_config.schema import PageDefinition
from forj_review_service.businesses.rules.scrap_certificate_qr import qr_candidates
from forj_review_service.contracts.recognition import CheckResult, RecognitionBatchResult
from forj_review_service.contracts.review import (
    FieldObservation,
    FieldStatus,
    ReviewRequest,
    ReviewResponse,
    ReviewTask,
)
from forj_review_service.evidence.comparison.aggregate import (
    aggregate_by_authority,
    aggregate_field,
)
from forj_review_service.integrations.qr.web_verifier import (
    QrCodeResult,
    QrWebVerifier,
    canonicalize_official_qr_url,
    validate_qr_url,
)
from forj_review_service.presentation.assembly import assemble_review_response
from forj_review_service.presentation.response import build_review_response
from forj_review_service.runtime.review_service import ReviewService
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO, business_registry
from tests.support.qr import qr_image_decoder


@pytest.fixture
def date_policy():
    profile = compile_page(
        PageDefinition.model_validate(
            {
                "page_id": "sample-date",
                "business_type": "sample",
                "title": "日期校验",
                "paths": ["/sample-date"],
                "scopes": ["sample"],
                "materials": [{"document_type": "permit", "label": "许可证"}],
                "fields": [
                    {
                        "key": "permit.issue_date",
                        "label": "发证日期",
                        "aliases": ["发证日期"],
                        "normalizer": "date",
                        "max_age_days": 90,
                        "evidence": [{"material": "permit", "field": "printed.date"}],
                    }
                ],
                "settings": {},
            }
        )
    )
    yield profile


def evidence(value, *, source="image", uncertain=False, confidence=0.95):
    return FieldObservation(
        field="permit.issue_date",
        source_type=source,
        source_id=source,
        document_type="permit" if source == "image" else None,
        value=value,
        uncertain=uncertain,
        confidence=confidence,
    )


@pytest.mark.parametrize(
    "days_ago, expected",
    [
        (0, FieldStatus.MATCH),
        (90, FieldStatus.MATCH),
        (91, FieldStatus.REVIEW_REQUIRED),
        (-1, FieldStatus.REVIEW_REQUIRED),
    ],
)
def test_date_policy_affects_raw_comparison_and_final_recommendation(
    days_ago, expected, date_policy
):
    issued = (
        datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=days_ago)
    ).isoformat()
    profile = replace(date_policy, material_policy=None)
    request = ReviewRequest(
        page_url="https://example.test/review",
        business_type="sample",
        page_fields={"permit.issue_date": issued},
    )
    response = build_review_response(
        request,
        RecognitionBatchResult(),
        profile,
        [evidence(issued), evidence(issued, source="page")],
        [],
    )
    assert response.comparisons[0].status is expected
    assert response.recommendation == (
        "PASS" if expected is FieldStatus.MATCH else "REVIEW_REQUIRED"
    )


def test_date_window_uses_declared_policy_and_rejects_unparseable_dates(date_policy):
    business = BusinessContext.from_profile(date_policy)
    assert (
        date_window_problem(
            "发证日期",
            "permit.issue_date",
            "2026-07-15",
            today=date(2026, 10, 13),
            business=business,
        )
        is None
    )
    assert "90 天" in date_window_problem(
        "发证日期", "permit.issue_date", "2026-07-14", today=date(2026, 10, 13), business=business
    )
    assert "日期格式" in date_window_problem(
        "发证日期", "permit.issue_date", "2026-02-30", business=business
    )


def test_equal_value_deduplication_retains_uncertainty():
    comparison = aggregate_field(
        "permit.issue_date",
        [
            evidence("2026-10-01"),
            evidence("2026-10-01", uncertain=True, confidence=0.4),
            evidence("2026-10-01", source="page"),
        ],
        business=TEST_BUSINESS,
    )
    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert len(comparison.evidence) == 2
    assert comparison.evidence[0].uncertain


def test_material_agreement_requires_page_evidence_when_reviewing_page_fields():
    comparison = aggregate_field(
        "permit.issue_date",
        [evidence("2026-10-01")],
        single_evidence_requires_review=False,
        page_evidence_required=True,
        business=TEST_BUSINESS,
    )
    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert "页面字段" in comparison.message


def test_authority_materials_cannot_pass_a_missing_page_field():
    observation = evidence("2026-10-01")
    authority = (AuthorityRule(source="image", label="行驶证", document_types=("permit",)),)
    comparison = aggregate_by_authority(
        "permit.issue_date",
        [observation],
        authority,
        page_evidence_required=True,
        business=TEST_BUSINESS,
    )
    assert comparison.status is FieldStatus.REVIEW_REQUIRED
    assert "页面字段" in comparison.message


def test_unmapped_control_cannot_leave_final_recommendation_pass():
    task = ReviewTask(
        step_id="FIELD-DOM-1",
        sequence=1,
        category="FIELD",
        display_target="ASSISTANT",
        requires_reviewer_action=True,
        label="未关联控件",
        result_status="INSUFFICIENT",
        reason="字段未能关联",
    )
    response = assemble_review_response(
        ReviewResponse(
            presentation="FIELD_WORKBENCH", recommendation="PASS", risk_level="LOW", summary=""
        ),
        RecognitionBatchResult(),
        business_checks=[],
        review_tasks=[task],
        business=TEST_BUSINESS,
    )
    assert response.recommendation == "REVIEW_REQUIRED"
    assert [finding.check_id for finding in response.agent_advice.findings] == [task.step_id]


def test_task_downgrade_is_not_masked_by_a_matching_underlying_check():
    task = ReviewTask(
        step_id="FIELD-DOM-1",
        check_ids=["CHECK-1"],
        sequence=1,
        category="FIELD",
        display_target="ASSISTANT",
        requires_reviewer_action=True,
        label="页面控件未关联",
        result_status="INSUFFICIENT",
        reason="页面值不可核验",
    )
    response = assemble_review_response(
        ReviewResponse(
            presentation="FIELD_WORKBENCH", recommendation="PASS", risk_level="LOW", summary=""
        ),
        RecognitionBatchResult(),
        business_checks=[
            CheckResult(check_id="CHECK-1", label="材料一致", status="MATCH", reason="材料一致")
        ],
        review_tasks=[task],
        business=TEST_BUSINESS,
    )
    assert response.recommendation == "REVIEW_REQUIRED"
    assert response.agent_advice.findings[0].check_id == task.step_id


@pytest.mark.asyncio
async def test_final_decision_log_matches_the_completed_business_result(caplog):
    with caplog.at_level("INFO", logger="forj_review_service"):
        response = await ReviewService(registry=business_registry()).assist_async(
            ReviewRequest(
                page_url="https://example.test/review",
                business_type="scrap_replacement",
                region="qingdao",
            )
        )
    decisions = [
        record.getMessage() for record in caplog.records if "review decision" in record.getMessage()
    ]
    assert len(decisions) == 1
    assert "note=final" in decisions[0]
    assert f"recommendation={response.recommendation.value}" in decisions[0]


@pytest.mark.asyncio
async def test_failed_preferred_qr_classification_still_scans_old_vehicle_fallback(monkeypatch):
    request = ReviewRequest(
        page_url="https://example.test/review",
        images=[
            {
                "index": 0,
                "src": "https://example.test/misclassified.jpg",
                "business_scope": "old_vehicle",
                "category_hint": "scrap_certificate",
            },
            {"index": 1, "src": "https://example.test/actual.jpg", "business_scope": "old_vehicle"},
        ],
    )
    service = ReviewService(registry=business_registry())
    service.qr_decoder = qr_image_decoder(
        {"https://example.test/actual.jpg": "https://qclt.mofcom.gov.cn/proof"}
    )
    from forj_review_service.contracts.recognition import RecognizedDocument

    batch = RecognitionBatchResult(
        recognized_documents=[
            RecognizedDocument(target_id="0", image_index=0, document_type="vehicle_license"),
            RecognizedDocument(
                target_id="1",
                image_index=1,
                document_type="scrap_certificate",
            ),
        ]
    )

    async def verify(code, index, **kwargs):
        return (
            QrCodeResult(
                index,
                code,
                url=code,
                domain_valid=True,
                accessible=True,
                status="MATCH",
                page_fields={"vin": "SYNTHETIC"},
            ),
            [],
        )

    monkeypatch.setattr(service.qr_web, "verify_with_retry", verify)
    result = await service._collect_qr_checks(request, batch, SCRAP_REPLACEMENT_QINGDAO)
    assert len(result) == 1 and result[0].domain_valid


@pytest.mark.parametrize(
    "url",
    [
        "https://qclt.mofcom.gov.cn:bad/x",
        "https://[broken/x",
        "https://user:password@qclt.mofcom.gov.cn/x",
        "https://qclt.mofcom.gov.cn:8443/x",
    ],
)
def test_malformed_or_unapproved_qr_urls_are_manual_review_inputs(url):
    assert canonicalize_official_qr_url(url, ["qclt.mofcom.gov.cn"]) is None
    assert not validate_qr_url(url, ["qclt.mofcom.gov.cn"])


@pytest.mark.asyncio
async def test_parallel_verifications_do_not_share_retry_state():
    parse_started, release_parse = (asyncio.Event(), asyncio.Event())
    requests = []

    async def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/parse":
            parse_started.set()
            await release_parse.wait()
            return httpx.Response(200, text="未能提取字段")
        return httpx.Response(503)

    verifier = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"], transport=httpx.MockTransport(handler)
    )
    parse = asyncio.create_task(verifier.verify_with_retry("https://qclt.mofcom.gov.cn/parse"))
    await parse_started.wait()
    await verifier.verify_with_retry("https://qclt.mofcom.gov.cn/transient", retry_count=0)
    release_parse.set()
    result, attempts = await parse
    assert result.accessible is True
    assert len(attempts) == requests.count("/parse") == 1


@pytest.mark.asyncio
async def test_qr_web_body_is_bounded_while_reading():
    read_chunks = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(10):
                read_chunks.append(index)
                yield b"0123456789"

    verifier = QrWebVerifier(
        allowed_hosts=["qclt.mofcom.gov.cn"],
        max_bytes=15,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body())),
    )
    result = await verifier.verify("https://qclt.mofcom.gov.cn/body")
    assert result.accessible is False
    assert read_chunks == [0, 1]


def test_qr_material_hints_cannot_override_explicit_new_vehicle_scope():
    request = ReviewRequest(
        page_url="https://example.test/review",
        images=[
            {
                "index": 0,
                "src": "https://example.test/qr.jpg",
                "category_hint": "scrap_certificate",
                "business_scope": "new_vehicle",
            }
        ],
    )
    assert qr_candidates(request, RecognitionBatchResult(), SCRAP_REPLACEMENT_QINGDAO) == ([], [])


def test_active_cancel_releases_scheduler_slot_for_another_review():
    scheduler = FairAsyncScheduler(global_limit=1, per_job_limit=1)
    started, cancelled = (threading.Event(), threading.Event())

    async def blocking():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def next_review():
        return "next_review_completed"

    first = scheduler.submit("cancelled-review", blocking)
    assert started.wait(2)
    next_future = scheduler.submit("next-review", next_review)
    scheduler.cancel("cancelled-review")
    assert cancelled.wait(2)
    assert next_future.result(timeout=2) == "next_review_completed"
    assert first.cancelled()

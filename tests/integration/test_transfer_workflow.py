"""新版过户审核贯穿主图；合成票据，不调用模型、数据库或业务接口。"""

import json
from pathlib import Path

import pytest

from forj_review_service.businesses.completeness import evaluate_extracted
from forj_review_service.businesses.materials import DOCUMENT_POLICIES
from forj_review_service.businesses.packs.model import build_collect_manifest
from forj_review_service.businesses.packs.transfer import (
    NEW_FIELDS,
    NEW_KEYS,
    NEW_PREFIX,
    NEW_SCOPE,
    TRANSFER_PACK,
    USED_FIELDS,
    USED_KEYS,
    USED_PREFIX,
    USED_SCOPE,
)
from forj_review_service.businesses.profiles import TRANSFER_DEFAULT as PROFILE
from forj_review_service.businesses.routing import route_fields
from forj_review_service.businesses.rules.transfer_documents import run_transfer_documents
from forj_review_service.capabilities.specs import ReviewExecutionContext
from forj_review_service.contracts.evidence import FieldObservation
from forj_review_service.contracts.recognition import RecognitionBatchResult, RecognizedDocument
from forj_review_service.contracts.review import ReviewFieldSnapshot, ReviewRequest
from forj_review_service.runtime.review_service import ReviewService


def fixture():
    used = dict(zip(USED_KEYS, (
        "2026-09-16", "冀A12345", "LFV00000000000001", "合成买方有限公司",
        "911300000000000001", "26130000000000000001", "10000.00",
        "合成卖方有限公司", "911300000000000002", "东风牌", "合成市车管所",
        "合成二手车市场有限公司", "911300000000000003",
    ), strict=True))
    new = dict(zip(NEW_KEYS, (
        "2026-09-03", "26130000000000000002", "260000.00", "LFV00000000000002",
    ), strict=True))
    documents = (
        ("registration_certificate", USED_SCOPE, {
            "transfer_registration.vin": used[f"{USED_PREFIX}.vin"], "registration.covered_pages": [1, 2],
        }),
        ("used_car_invoice", USED_SCOPE, {
            **used,
        }),
        ("invoice", NEW_SCOPE, {
            **new,
        }),
    )
    observations = []
    for index, (kind, scope, fields) in enumerate(documents):
        routed, issue = route_fields(scope, kind, fields, TRANSFER_PACK)
        assert issue is None
        observations.extend(FieldObservation(
            field=field, value=value, source_type="image", source_id=f"doc-{index}",
            image_id=f"doc-{index}", image_index=index, document_type=kind, business_scope=scope,
        ) for field, value in routed.items())
    values = {**used, **new}
    request = ReviewRequest(
        business_type="transfer", region="default", page_id="transfer:default",
        workflow_stage="transfer", page_url="https://example.test/consistency-qingdao/review/1",
        page_fields=values,
        review_fields=[ReviewFieldSnapshot(
            field=f"{prefix}.{name}", label=label, value=values[f"{prefix}.{name}"], order=i,
            control_type="date" if name == "invoice_date" else "text",
            editable=True,
        ) for i, (prefix, (name, label, *_)) in enumerate([
            *((USED_PREFIX, item) for item in USED_FIELDS),
            *((NEW_PREFIX, item) for item in NEW_FIELDS),
        ], 1)],
        images=[{"index": i, "src": f"https://example.test/doc-{i}.jpg", "imageId": f"doc-{i}",
                 "businessScope": scope, "documentTypeHint": kind}
                for i, (kind, scope, _) in enumerate(documents)],
    )
    batch = RecognitionBatchResult(
        total_count=3, completed_count=3, observations=observations,
        recognized_documents=[RecognizedDocument(
            target_id=f"doc-{i}", image_index=i, document_type=kind, business_scope=scope,
            covered_pages=[1, 2] if kind == "registration_certificate" else [],
        ) for i, (kind, scope, _) in enumerate(documents)],
    )
    return request, batch


def special_checks(request, batch):
    return run_transfer_documents(ReviewExecutionContext(
        request, PROFILE, batch, tuple(batch.observations),
    )).checks


def test_manifest_and_material_prompts_keep_the_two_invoices_separate():
    manifest = build_collect_manifest(TRANSFER_PACK)
    assert manifest == json.loads(Path(
        "apps/review-extension/tests/fixtures/collect-manifest-transfer.json",
    ).read_text(encoding="utf-8"))
    assert {f["key"] for f in manifest["fields"] if f["reviewable"]} == set(USED_KEYS + NEW_KEYS)
    assert set(manifest["writable_fields"]) == set(USED_KEYS + NEW_KEYS)
    assert len(USED_KEYS + NEW_KEYS) == 17
    assert TRANSFER_PACK.material("invoice").derived_field is None
    assert not TRANSFER_PACK.page_field_composites
    assert not PROFILE.invoice_verification_field  # 验真交给业务页面各自的按钮。
    for kind, scope, prefix in (("used_car_invoice", USED_SCOPE, USED_PREFIX),
                                ("invoice", NEW_SCOPE, NEW_PREFIX)):
        fields = DOCUMENT_POLICIES[kind].fields_for_scope(scope)
        assert all(field.startswith(prefix + ".") for field in fields)
        prompt = DOCUMENT_POLICIES[kind].build_extraction_prompt(1, scope)
        assert "不得" in prompt or "不能" in prompt
        assert "code_layout" not in fields
        assert f"{prefix}.code" not in fields


@pytest.mark.parametrize("kind,scope", [("invoice", USED_SCOPE), ("used_car_invoice", NEW_SCOPE)])
def test_misplaced_invoice_never_routes_to_the_other_vehicle(kind, scope):
    fields, issue = route_fields(scope, kind, {"transfer_invoice.amount": "999"}, TRANSFER_PACK)
    assert not fields and issue


@pytest.mark.parametrize("missing", [None, 0, 1, 2])
def test_upload_presence_is_independent_of_ocr_and_missing_pages(missing):
    request, batch = fixture()
    request.images = [image for i, image in enumerate(request.images) if i != missing]
    report = evaluate_extracted(request, PROFILE, RecognitionBatchResult(failed_count=3))
    assert report.presence.status == ("COMPLETE" if missing is None else "INCOMPLETE")
    assert len(report.presence.groups) == 3


@pytest.mark.parametrize("problem", ["different", "uncertain", "missing", "short", "duplicate"])
def test_registration_vin_problem_keeps_invoice_candidate_available_for_reviewer(problem):
    request, batch = fixture()
    item = next(o for o in batch.observations if o.field == "transfer_registration.vin")
    if problem == "different":
        item.value = "LFV00000000000009"
    elif problem == "uncertain":
        item.uncertain = True
    elif problem == "missing":
        batch.observations.remove(item)
    elif problem == "short":
        item.value = "00000001"
    else:
        batch.observations.append(item.model_copy(update={"value": "LFV00000000000009"}))
    check = special_checks(request, batch)[0]
    assert check.status != "MATCH"
    assert "LFV00000000000001" in check.details["permitted_fill_values"]
    if problem in {"different", "duplicate"}:
        assert "LFV00000000000009" in check.details["permitted_fill_values"]
    if problem == "short":
        assert "00000001" not in check.details["permitted_fill_values"]
    assert "不能按片段核对或回填" not in check.reason


def test_old_vin_agreed_candidates_remain_deduplicated():
    request, batch = fixture()
    request.page_fields[f"{USED_PREFIX}.vin"] = "LFV00000000000009"
    check = special_checks(request, batch)[0]
    assert check.status == "CONFLICT"
    assert check.details["permitted_fill_values"] == ["LFV00000000000001"]
    assert len(check.values) == 2


def test_old_vin_material_conflict_keeps_each_values_actual_relation_to_page():
    request, batch = fixture()
    next(o for o in batch.observations if o.field == "transfer_registration.vin").value = "LFV00000000000009"
    check = special_checks(request, batch)[0]
    assert check.status == "CONFLICT"
    assert set(check.details["permitted_fill_values"]) == {"LFV00000000000001", "LFV00000000000009"}
    by_source = {value.source: value for value in check.values}
    assert by_source["二手车发票"].comparison_status == "MATCH"
    assert by_source["二手车发票"].conflicting is False
    assert by_source["登记证书1、2页"].comparison_status == "CONFLICT"


def test_invoice_codes_are_excluded_even_when_page_and_ocr_supply_them():
    for prefix in (USED_PREFIX, NEW_PREFIX):
        assert TRANSFER_PACK.field(f"{prefix}.code") is None
        assert f"{prefix}.code" not in TRANSFER_PACK.writable_field_keys()
    assert len(PROFILE.field_check_bindings) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "used_amount", "new_amount", "old_vin", "new_vin", "uncertain"])
async def test_full_workflow_preserves_sources_and_controls(monkeypatch, change):
    request, batch = fixture()
    changed_field = None
    if change == "used_amount":
        changed_field = f"{USED_PREFIX}.amount"
        request.page_fields[changed_field] = "260000.00"
    elif change == "new_amount":
        changed_field = f"{NEW_PREFIX}.amount"
        request.page_fields[changed_field] = "230088.50"
    elif change == "old_vin":
        changed_field = f"{USED_PREFIX}.vin"
        next(o for o in batch.observations if o.field == "transfer_registration.vin").value = "LFV00000000000009"
    elif change == "new_vin":
        changed_field = f"{NEW_PREFIX}.vin"
        request.page_fields[changed_field] = request.page_fields[f"{USED_PREFIX}.vin"]
    elif change == "uncertain":
        changed_field = f"{USED_PREFIX}.buyer_id"
        next(o for o in batch.observations if o.field == changed_field).uncertain = True
    # System context must never create extra material checks.
    request.page_fields["context.dealer"] = "合成经销商"
    for snapshot in request.review_fields:
        snapshot.value = request.page_fields[snapshot.field]
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    fields = {t.page_target_field: t for t in result.review_tasks if t.category == "FIELD"}
    assert set(fields) == set(USED_KEYS + NEW_KEYS)
    assert len(result.review_tasks) == 17
    assert list(fields) == [s.field for s in sorted(request.review_fields, key=lambda s: s.order)]
    assert result.material_completeness.presence.status == "COMPLETE"
    assert not result.page_actions  # 不点击验真，也不提交审核。
    for field, task in fields.items():
        assert task.page_value == request.page_fields[field]
        if field == changed_field:
            assert task.result_status != "MATCH"
        else:
            assert task.result_status == "MATCH", (field, task.reason)
    assert fields[f"{USED_PREFIX}.invoice_date"].page_value == "2026-09-16"
    assert fields[f"{NEW_PREFIX}.invoice_date"].page_value == "2026-09-03"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["date", "vin", "both", "ambiguous"])
async def test_missing_date_or_vin_inventory_never_silently_drops_a_review_field(monkeypatch, missing):
    request, batch = fixture()
    absent = {f"{USED_PREFIX}.invoice_date"} if missing == "date" else {f"{USED_PREFIX}.vin"}
    if missing in {"both", "ambiguous"}:
        absent = {f"{USED_PREFIX}.invoice_date", f"{USED_PREFIX}.vin"}
    if missing == "ambiguous":
        for snapshot in request.review_fields:
            if snapshot.field in absent:
                snapshot.field = None
    else:
        request.review_fields = [s for s in request.review_fields if s.field not in absent]
    for field in absent:
        request.page_fields.pop(field)
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    tasks = [t for t in result.review_tasks if t.category == "FIELD"]
    assert [t.page_target_field for t in tasks] == list(USED_KEYS + NEW_KEYS)
    for task in tasks:
        if task.page_target_field in absent:
            assert task.result_status == "INSUFFICIENT"
            assert not task.writable
            assert "PAGE_VALUE_MISSING" in task.reason_codes


@pytest.mark.asyncio
async def test_actual_dom_order_wins_and_invoice_code_controls_do_not_create_tasks(monkeypatch):
    request, batch = fixture()
    # 模拟新车区在 DOM 中先出现；不能按比较器的字段名顺序重排。
    request.review_fields.reverse()
    for order, snapshot in enumerate(request.review_fields, 1):
        snapshot.order = order
    for prefix in (USED_PREFIX, NEW_PREFIX):
        request.page_fields[f"{prefix}.code"] = "不核对"
        request.review_fields.append(ReviewFieldSnapshot(
            field=None, label="发票代码" if prefix == USED_PREFIX else "新车发票代码",
            value="不核对", order=99, control_type="text", editable=True,
        ))
    request.page_fields[f"{NEW_PREFIX}.style"] = "电子"
    request.review_fields.append(ReviewFieldSnapshot(
        field=None, label="新车发票类型", value="电子", order=100,
        control_type="select", editable=True,
    ))
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    tasks = [t for t in result.review_tasks if t.category == "FIELD"]
    assert [t.page_target_field for t in tasks] == list(reversed(USED_KEYS + NEW_KEYS))
    assert all("代码" not in t.label for t in tasks)
    assert all(t.label != "新车发票类型" for t in tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", USED_KEYS + NEW_KEYS)
async def test_collected_empty_control_allows_fill_in_full_workflow(monkeypatch, field):
    request, batch = fixture()
    request.page_fields[field] = ""
    next(s for s in request.review_fields if s.field == field).value = ""
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    task = next(t for t in result.review_tasks if t.page_target_field == field)
    assert task.page_value == ""
    assert task.writable
    assert task.result_status != "MATCH"
    if field == f"{USED_PREFIX}.vin":
        assert task.details["permitted_fill_values"] == ["LFV00000000000001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [f"{USED_PREFIX}.invoice_no", f"{USED_PREFIX}.vin"])
async def test_ambiguous_collection_does_not_offer_fill_even_with_a_control_snapshot(monkeypatch, field):
    request, batch = fixture()
    request.collection_diagnostics.ambiguous_fields = [field]
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    task = next(t for t in result.review_tasks if t.page_target_field == field)
    assert task.result_status != "MATCH"
    assert not task.writable
    assert task.page_value is None


@pytest.mark.asyncio
@pytest.mark.parametrize("registration_value", [None, "LGB42308617", "LFV00000000000009"])
async def test_reviewer_can_fill_invoice_vin_despite_registration_problem(monkeypatch, registration_value):
    request, batch = fixture()
    registration = next(o for o in batch.observations if o.field == "transfer_registration.vin")
    if registration_value is None:
        batch.observations.remove(registration)
    else:
        registration.value = registration_value
    field = f"{USED_PREFIX}.vin"
    # 页面漏了一位；完整发票值依然是独立的比对和回填候选。
    request.page_fields[field] = "LFV0000000000001"
    next(s for s in request.review_fields if s.field == field).value = request.page_fields[field]
    service = ReviewService(load_model=False)

    async def extract(*_args, **_kwargs):
        return batch

    monkeypatch.setattr(service, "_extract_documents", extract)
    result = await service.assist_async(request)
    task = next(t for t in result.review_tasks if t.page_target_field == field)
    assert task.writable
    assert "LFV00000000000001" in task.details["permitted_fill_values"]
    invoice = next(v for v in task.values if v.source == "二手车发票")
    assert invoice.comparison_status == "CONFLICT"
    assert "不能按片段核对或回填" not in invoice.check_reason
    # 回填页面不消除材料本身的问题，也不能因此变为一致。
    request.page_fields[field] = "LFV00000000000001"
    next(s for s in request.review_fields if s.field == field).value = request.page_fields[field]
    result = await service.assist_async(request)
    task = next(t for t in result.review_tasks if t.page_target_field == field)
    assert task.result_status != "MATCH"
    assert next(v for v in task.values if v.source == "二手车发票").comparison_status == "MATCH"

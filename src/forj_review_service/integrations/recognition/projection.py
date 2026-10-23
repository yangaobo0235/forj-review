"""模型提取结果到领域文档、观察值和原图区域的纯映射。"""

import re
from collections.abc import Mapping
from typing import Any

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.materials import DocumentPolicy
from forj_review_service.businesses.routing import route_fields
from forj_review_service.contracts.recognition import QwenExtraction, RecognizedDocument
from forj_review_service.contracts.review import FieldObservation, ImageInput


def _derived_from(
    policy: DocumentPolicy, field_name: str, extracted_fields: Mapping[str, Any]
) -> str | None:
    """该字段的值是不是从票面另一个号码派生的；是则返回那个号码的字段键。

    数电发票票面只有一个号码，页面上的「发票代码」由兼容层补齐。派生关系写在
    材料声明里（`MaterialDeclaration.derived_field`），这里只负责查。
    """
    derived = getattr(policy, "derived_field", None)
    if derived is None:
        return None
    number_field, derived_field = derived
    if field_name == derived_field and number_field in extracted_fields:
        return number_field
    return None


def _covered_pages(value: Any) -> list[int]:
    """Normalize page numbers returned as arrays or common OCR text variants."""

    values = value if isinstance(value, list) else [value]
    pages: set[int] = set()
    for item in values:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            number = int(item)
            if number > 0:
                pages.add(number)
            continue
        for raw in re.findall(r"(?:第\s*)?(\d{1,2})(?:\s*页)?", str(item)):
            number = int(raw)
            if number > 0:
                pages.add(number)
    return sorted(pages)


def build_extracted_evidence(
    image: ImageInput,
    extraction: QwenExtraction,
    policy: DocumentPolicy,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> tuple[RecognizedDocument, list[FieldObservation], str | None]:
    """仅做声明驱动的字段/区域路由；重试和批次计数由调用方管理。

    原始不确定字段保留在文档中，由证据收集入口统一传播到规范字段。
    """
    image_name = image.image_id or image.index
    uncertain_fields = set(extraction.uncertain_fields)
    for key in extraction.fields:
        confidence = extraction.field_confidences.get(key, extraction.confidence)
        if confidence is not None and confidence < 0.7:
            uncertain_fields.add(key)
    document = RecognizedDocument(
        target_id=str(image_name),
        image_index=image.index,
        document_type=policy.document_type,
        business_scope=image.business_scope,
        covered_pages=_covered_pages(extraction.fields.get("registration.covered_pages")),
        uncertain_fields=sorted(uncertain_fields),
        raw_fields=list(extraction.raw_fields),
        uncertain_values={field: extraction.fields.get(field) for field in uncertain_fields},
        qr_codes=list(dict.fromkeys(code.strip() for code in extraction.qr_codes if code.strip())),
    )

    routed_fields, routing_limitation = route_fields(
        image.business_scope,
        policy.document_type,
        extraction.fields,
        group_order=image.group_order,
        pack=business.pack,
    )
    routed_regions: dict[str, list[float]] = {}
    routed_confidences, _ = route_fields(
        image.business_scope,
        policy.document_type,
        extraction.field_confidences,
        group_order=image.group_order,
        pack=business.pack,
    )
    readings = list(routed_fields.items())
    conflicting_keys = set()
    for key, values in extraction.field_alternatives.items():
        for value in values:
            extra, _ = route_fields(
                image.business_scope,
                policy.document_type,
                {key: value},
                group_order=image.group_order,
                pack=business.pack,
            )
            readings.extend(item for item in extra.items() if item not in readings)
            conflicting_keys.update(extra)
    routed_uncertainty, _ = route_fields(
        image.business_scope,
        policy.document_type,
        dict.fromkeys(uncertain_fields, True),
        group_order=image.group_order,
        pack=business.pack,
    )
    for region in extraction.evidence_regions:
        region_fields, _ = route_fields(
            image.business_scope,
            policy.document_type,
            {region.field: "__evidence_region__"},
            group_order=image.group_order,
            pack=business.pack,
        )
        for routed_field in region_fields:
            routed_regions.setdefault(routed_field, list(region.box))
    observations = [
        FieldObservation(
            field=field_name,
            source_type="image",
            source_id=str(image_name),
            document_type=policy.document_type,
            image_index=image.index,
            image_id=str(image_name),
            business_scope=image.business_scope,
            group_title=image.group_title,
            group_order=image.group_order,
            value=value,
            derived_from=_derived_from(policy, field_name, extraction.fields),
            evidence_region=None
            if field_name in conflicting_keys
            else routed_regions.get(field_name),
            uncertain=field_name in routed_uncertainty,
            confidence=routed_confidences.get(field_name, extraction.confidence),
        )
        for field_name, value in readings
        if value
    ]
    return document, observations, routing_limitation

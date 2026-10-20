"""旧车、新车主体类型及营业执照法人关系检查。"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.routing import normalize_document_type
from forj_review_service.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_service.contracts.checks import CheckResultValue
from forj_review_service.contracts.recognition import CheckResult
from forj_review_service.contracts.review import FieldObservation
from forj_review_service.evidence.comparison.evidence_values import (
    observation_evidence,
    readable_value,
)
from forj_review_service.evidence.comparison.settled_values import raw_settled_value
from forj_review_service.evidence.fields.normalize import normalize_value

COMPANY_PATTERN = re.compile(r"(?:有限责任公司|股份有限公司|有限公司|公司|企业|合作社|经营部|厂)$")
PERSON_PATTERN = re.compile(r"^[\u3400-\u9fff]{2,4}$")
AFFILIATION_DOCUMENT_TYPES = frozenset({"business_license", "identity_card"})
AFFILIATION_VALUES = {"business_license": "公司", "identity_card": "个人"}


@dataclass(frozen=True)
class BusinessLicenseEvidence:
    source_id: str
    company_name: str | None
    legal_representative: str | None
    unified_social_credit_code: str | None
    ambiguous: bool = False
    company_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityCardEvidence:
    source_id: str
    side: str | None
    name: str | None
    ambiguous: bool = False


@dataclass(frozen=True)
class AffiliationCheckResult:
    check: CheckResult
    owner_types: tuple[str | None, str | None]


def affiliation_material_value(
    observations: Iterable[FieldObservation] = (),
    *,
    image_document_types: Iterable[object] = (),
    recognized_document_types: Iterable[object] = (),
) -> str | None:
    """Return the only safe affiliation value implied by uploaded materials.

    A single business license implies ``公司`` and a single identity-card
    material implies ``个人``.  If both material types are present, or the
    material type cannot be established, no automatic write-back is allowed.
    The caller may provide both recognized evidence and upload/classification
    hints because OCR can fail after a material was uploaded.
    """

    document_types: set[str] = set()
    for value in image_document_types:
        if value is None:
            continue
        normalized = normalize_document_type(str(value).strip())
        if normalized in AFFILIATION_DOCUMENT_TYPES:
            document_types.add(normalized)
    for value in recognized_document_types:
        if value is None:
            continue
        normalized = normalize_document_type(str(value).strip())
        if normalized in AFFILIATION_DOCUMENT_TYPES:
            document_types.add(normalized)
    for item in observations:
        normalized = normalize_document_type(str(item.document_type or "").strip())
        if normalized in AFFILIATION_DOCUMENT_TYPES:
            document_types.add(normalized)
    if len(document_types) != 1:
        return None
    return AFFILIATION_VALUES[next(iter(document_types))]


def _name(value: object | None, *, business: BusinessContext = DEFAULT_BUSINESS) -> str | None:
    normalized = normalize_value("party.owner", value, business=business)
    return normalized if readable_value(normalized) else None


def _person_name(
    value: object | None, *, business: BusinessContext = DEFAULT_BUSINESS
) -> str | None:
    name = _name(value, business=business)
    return (
        name
        if name and PERSON_PATTERN.fullmatch(name) and not COMPANY_PATTERN.search(name)
        else None
    )


def _identity_pair_key(source_id: str) -> str:
    return re.sub(r"[-_](?:front|back)$", "", source_id, flags=re.IGNORECASE)


def group_business_licenses(
    observations: list[FieldObservation],
) -> list[BusinessLicenseEvidence]:
    grouped: dict[str, dict[str, set[str]]] = {}
    uncertain_sources: set[str] = set()
    for item in observations:
        if item.source_type != "image" or item.document_type != "business_license":
            continue
        if not item.field.startswith("business_license.") or item.value in (None, ""):
            continue
        if item.uncertain:
            uncertain_sources.add(item.source_id)
        grouped.setdefault(item.source_id, {}).setdefault(item.field, set()).add(str(item.value))
    return [
        BusinessLicenseEvidence(
            source_id=source_id,
            company_name=next(iter(values.get("business_license.company_name", set())), None),
            legal_representative=next(
                iter(values.get("business_license.legal_representative", set())), None
            ),
            unified_social_credit_code=next(
                iter(values.get("business_license.unified_social_credit_code", set())),
                None,
            ),
            ambiguous=source_id in uncertain_sources
            or any(len(field_values) > 1 for field_values in values.values()),
            company_names=tuple(sorted(values.get("business_license.company_name", set()))),
        )
        for source_id, values in grouped.items()
    ]


def group_identity_cards(
    observations: list[FieldObservation],
) -> list[IdentityCardEvidence]:
    grouped: dict[str, dict[str, set[str]]] = {}
    uncertain_sources: set[str] = set()
    for item in observations:
        if item.source_type != "image" or item.document_type != "identity_card":
            continue
        if not item.field.startswith("identity_card.") or item.value in (None, ""):
            continue
        if item.uncertain:
            uncertain_sources.add(item.source_id)
        grouped.setdefault(item.source_id, {}).setdefault(item.field, set()).add(
            str(item.value).strip()
        )
    return [
        IdentityCardEvidence(
            source_id=source_id,
            side=next(iter(values.get("identity_card.side", set())), None),
            name=next(iter(values.get("identity_card.name", set())), None),
            ambiguous=source_id in uncertain_sources
            or any(len(field_values) > 1 for field_values in values.values()),
        )
        for source_id, values in grouped.items()
    ]


def _license_for(
    owner: str,
    licenses: list[BusinessLicenseEvidence],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> BusinessLicenseEvidence | None:
    matches = [
        item
        for item in licenses
        if owner
        in {_name(name, business=business) for name in item.company_names or (item.company_name,)}
    ]
    return matches[0] if len(matches) == 1 and not matches[0].ambiguous else None


def classify_owner_type(
    owner: object | None,
    licenses: list[BusinessLicenseEvidence],
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> str | None:
    normalized = _name(owner, business=business)
    if not normalized:
        return None
    if _license_for(normalized, licenses, business=business) is not None or COMPANY_PATTERN.search(
        normalized
    ):
        return "COMPANY"
    if PERSON_PATTERN.fullmatch(normalized):
        return "PERSONAL"
    return None


def _auxiliary_check(
    check_id: str,
    label: str,
    page_value: object | None,
    material_value: object | None,
    *,
    field: str,
    page_source: str,
    material_source: str,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> CheckResult:
    values = [
        CheckResultValue(source=page_source, value=page_value),
        CheckResultValue(source=material_source, value=material_value),
    ]
    normalized_page = normalize_value(field, page_value, business=business)
    normalized_material = normalize_value(field, material_value, business=business)
    if not normalized_page or not normalized_material:
        return CheckResult(
            check_id=check_id,
            label=label,
            status="INSUFFICIENT",
            reason="页面或材料未取得可比较的明确值",
            values=values,
        )
    return CheckResult(
        check_id=check_id,
        label=label,
        status="MATCH" if normalized_page == normalized_material else "CONFLICT",
        reason=(
            "页面字段与材料识别结果一致"
            if normalized_page == normalized_material
            else "页面字段与材料识别结果不一致"
        ),
        values=values,
    )


def normalize_page_owner_type(
    value: object | None, *, business: BusinessContext = DEFAULT_BUSINESS
) -> str | None:
    """Only recognize the page's explicit personal/company option labels."""

    normalized = normalize_value("application.owner_type", value, business=business)
    if normalized == "个人":
        return "PERSONAL"
    if normalized in {"公司", "企业"}:
        return "COMPANY"
    return None


def build_affiliation_auxiliary_checks(
    *,
    page_fields: dict[str, object],
    new_owner: object | None,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> tuple[CheckResult, ...]:
    """Check customer identity; vehicle VINs are independent field reviews."""

    customer = _auxiliary_check(
        "AFFILIATION-AUX-CUSTOMER-NAME",
        "客户名称",
        page_fields.get("application.customer_name"),
        new_owner,
        field="new_vehicle.owner",
        page_source="申请页面客户名称",
        material_source="新车材料所有人",
        business=business,
    )
    return (customer,)


def build_affiliation_subject_check(
    old_owner: object | None,
    new_owner: object | None,
    observations: list[FieldObservation],
    page_owner_type: object | None = None,
    *,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> AffiliationCheckResult:
    licenses = group_business_licenses(observations)
    identities = group_identity_cards(observations)
    old_name = _name(old_owner, business=business)
    new_name = _name(new_owner, business=business)
    old_type = classify_owner_type(old_owner, licenses, business=business)
    inferred_new_type = classify_owner_type(new_owner, licenses, business=business)
    declared_new_type = normalize_page_owner_type(page_owner_type, business=business)
    new_type = declared_new_type or inferred_new_type
    values = [
        CheckResultValue(source="旧车所有人", value=old_owner),
        CheckResultValue(source="新车所有人", value=new_owner),
        CheckResultValue(source="旧车主体类型", value=old_type),
        CheckResultValue(source="新车主体类型", value=new_type),
    ]
    relevant_license_sources = {
        item.source_id
        for item in observations
        if item.field == "business_license.company_name"
        and _name(item.value, business=business) in {old_name, new_name}
    }
    personal_names = {
        name
        for name, owner_type in ((old_name, old_type), (new_name, new_type))
        if owner_type == "PERSONAL" and name
    }
    relevant_identity_sources = {
        item.source_id for item in identities if item.name in personal_names or item.side == "BACK"
    }
    evidence = observation_evidence(
        [
            item
            for item in observations
            if item.field in {"old_vehicle.owner", "new_vehicle.owner"}
            or item.source_id in relevant_license_sources
            and item.field
            in {
                "business_license.company_name",
                "business_license.legal_representative",
                "business_license.unified_social_credit_code",
            }
            or item.source_id in relevant_identity_sources
            and item.field in {"identity_card.name", "identity_card.side"}
        ]
    )
    status = "INSUFFICIENT"
    reason = "旧车或新车主体类型无法可靠判断"

    requirements: list[dict[str, object]] = []
    missing_requirements: list[str] = []
    distinct_subjects: list[tuple[str, str, str]] = []
    for party, name, owner_type in (
        ("OLD_VEHICLE", old_name, old_type),
        ("NEW_VEHICLE", new_name, new_type),
    ):
        if not name or not owner_type:
            continue
        existing_index = next(
            (
                index
                for index, (_, existing_name, existing_type) in enumerate(distinct_subjects)
                if existing_name == name and existing_type == owner_type
            ),
            None,
        )
        if existing_index is not None:
            distinct_subjects[existing_index] = ("SHARED", name, owner_type)
            continue
        distinct_subjects.append((party, name, owner_type))

    identity_backs = [item for item in identities if item.side == "BACK" and not item.ambiguous]
    personal_subject_count = sum(owner_type == "PERSONAL" for _, _, owner_type in distinct_subjects)
    same_company_subject = bool(
        old_type == new_type == "COMPANY" and old_name and old_name == new_name
    )
    for party, name, owner_type in distinct_subjects:
        if owner_type == "COMPANY":
            matching_licenses = [
                item
                for item in licenses
                if name
                in {
                    _name(candidate, business=business)
                    for candidate in item.company_names or (item.company_name,)
                }
            ]
            license_item = _license_for(name, licenses, business=business)
            requirement_status = (
                "PRESENT"
                if license_item
                and (
                    same_company_subject
                    or _person_name(license_item.legal_representative, business=business)
                )
                else "UNCERTAIN"
                if matching_licenses
                else "MISSING"
            )
            requirement_reason = (
                "营业执照名称已确认"
                if requirement_status == "PRESENT" and same_company_subject
                else "营业执照名称和法定代表人已确认"
                if requirement_status == "PRESENT"
                else "营业执照存在多份、冲突或法定代表人无法确认"
                if requirement_status == "UNCERTAIN"
                else f"缺少与{name}对应的营业执照"
            )
            requirements.append(
                {
                    "party": party,
                    "subject_name": name,
                    "subject_type": owner_type,
                    "document": "business_license",
                    "status": requirement_status,
                    "image_ids": [item.source_id for item in matching_licenses],
                    "reason": requirement_reason,
                }
            )
            if requirement_status != "PRESENT":
                missing_requirements.append(requirement_reason)
            continue

        matching_fronts = [
            item
            for item in identities
            if item.side == "FRONT"
            and _name(item.name, business=business) == name
            and not item.ambiguous
        ]
        front_status = (
            "PRESENT"
            if len(matching_fronts) == 1
            else "UNCERTAIN"
            if matching_fronts
            else "MISSING"
        )
        matching_backs = (
            identity_backs
            if personal_subject_count == 1
            else [
                item
                for item in identity_backs
                if any(
                    _identity_pair_key(item.source_id) == _identity_pair_key(front.source_id)
                    for front in matching_fronts
                )
            ]
        )
        back_status = (
            "PRESENT" if len(matching_backs) == 1 else "UNCERTAIN" if identity_backs else "MISSING"
        )
        for document, document_status, image_ids, document_label in (
            (
                "identity_card_front",
                front_status,
                [item.source_id for item in matching_fronts],
                "身份证正面",
            ),
            (
                "identity_card_back",
                back_status,
                [item.source_id for item in matching_backs or identity_backs],
                "身份证反面",
            ),
        ):
            requirement_reason = (
                f"{document_label}已确认"
                if document_status == "PRESENT"
                else "存在多个个人主体，身份证反面不含姓名，无法确认对应关系"
                if document == "identity_card_back"
                and document_status == "UNCERTAIN"
                and personal_subject_count > 1
                else f"{document_label}存在冲突或无法确认"
                if document_status == "UNCERTAIN"
                else f"{name}缺少{document_label}"
            )
            requirements.append(
                {
                    "party": party,
                    "subject_name": name,
                    "subject_type": owner_type,
                    "document": document,
                    "status": document_status,
                    "image_ids": image_ids,
                    "reason": requirement_reason,
                }
            )
            if document_status != "PRESENT":
                missing_requirements.append(requirement_reason)

    related_licenses = [
        item
        for item in licenses
        if {_name(name, business=business) for name in item.company_names or (item.company_name,)}
        & {old_name, new_name}
    ]
    invalid_license_evidence = any(
        item.ambiguous or not _person_name(item.legal_representative, business=business)
        for item in related_licenses
    )
    company_names = {
        name
        for name, owner_type in ((old_name, old_type), (new_name, new_type))
        if owner_type == "COMPANY" and name
    }
    duplicate_license_evidence = any(
        sum(
            name
            in {
                _name(candidate, business=business)
                for candidate in item.company_names or (item.company_name,)
            }
            for item in related_licenses
        )
        > 1
        for name in company_names
    )

    type_mismatch = bool(
        declared_new_type and inferred_new_type and declared_new_type != inferred_new_type
    )
    if type_mismatch:
        requirements = []
        reason = "页面车辆所有人类型与新车材料主体无法可靠对应，请人工确认"
    elif missing_requirements:
        reason = "；".join(dict.fromkeys(missing_requirements))
    elif old_type == new_type == "COMPANY" and old_name and old_name == new_name:
        status = "MATCH"
        reason = "新旧车公司法定名称一致且营业执照已确认"
    elif invalid_license_evidence or duplicate_license_evidence:
        reason = "对应公司营业执照存在多份、冲突或不确定法人证据，请核对全部来源"
    elif old_type and new_type and old_name and new_name:
        if old_type == new_type == "PERSONAL":
            status = "MATCH" if old_name == new_name else "CONFLICT"
            reason = (
                "新旧车个人姓名一致且身份证正反面已确认"
                if status == "MATCH"
                else "新旧车均为个人但姓名不同"
            )
        elif old_type == new_type == "COMPANY":
            old_license = _license_for(old_name, licenses, business=business)
            new_license = _license_for(new_name, licenses, business=business)
            if (
                not old_license
                or not new_license
                or old_license.source_id == new_license.source_id
                or not _person_name(old_license.legal_representative, business=business)
                or not _person_name(new_license.legal_representative, business=business)
            ):
                reason = "缺少分别对应两家公司的完整营业执照法人证据"
            elif _name(old_license.legal_representative, business=business) == _name(
                new_license.legal_representative, business=business
            ):
                status = "MATCH"
                reason = "两家公司营业执照分别对应且法人相同"
            else:
                status = "CONFLICT"
                reason = "两家公司营业执照法人不同"
        else:
            company_name = old_name if old_type == "COMPANY" else new_name
            person_name = old_name if old_type == "PERSONAL" else new_name
            license_item = _license_for(company_name, licenses, business=business)
            if not license_item or not _person_name(
                license_item.legal_representative, business=business
            ):
                reason = "缺少与公司对应的营业执照法人证据"
            elif _name(license_item.legal_representative, business=business) == person_name:
                status = "MATCH"
                reason = "公司营业执照法人等于个人姓名"
            else:
                status = "CONFLICT"
                reason = "公司营业执照法人与个人姓名不同"

    return AffiliationCheckResult(
        check=CheckResult(
            check_id="AFFILIATION-SUBJECT-001",
            label="新旧车挂靠主体关系",
            status=status,
            reason=reason,
            values=values,
            evidence=evidence,
            details={"subject_requirements": requirements},
        ),
        owner_types=(old_type, new_type),
    )


def run_affiliation_subject(context: ReviewExecutionContext) -> RuleExecutionResult:
    """主体关系检查；页面挂靠写回由独立的材料主体动作负责。"""
    business = BusinessContext.from_profile(context.profile)
    page_fields = {
        key: value
        for key, value in context.request.page_fields.items()
        if key not in context.request.collection_diagnostics.ambiguous_fields
    }
    comparisons = {item.field: item for item in context.comparisons}
    result = build_affiliation_subject_check(
        page_fields.get("old_vehicle.owner") or raw_settled_value(comparisons, "old_vehicle.owner"),
        page_fields.get("new_vehicle.owner") or raw_settled_value(comparisons, "new_vehicle.owner"),
        list(context.observations),
        page_fields.get("application.owner_type"),
        business=business,
    )
    auxiliary_checks = build_affiliation_auxiliary_checks(
        page_fields=page_fields,
        new_owner=page_fields.get("new_vehicle.owner")
        or raw_settled_value(comparisons, "new_vehicle.owner"),
        business=business,
    )
    return RuleExecutionResult(
        checks=(result.check, *auxiliary_checks),
    )

"""报废置换字段的动态证据来源策略。

策略只描述允许的来源和比较语义，不把某一张材料当成所有申请都必须存在的
“主要来源”。运行时仍以实际识别到的有效证据为准。

字段的裁决方式有两种，都是声明式的：

- `mode="SYSTEM"`：以页面填写值为准，材料不参与比对（如手机号、主体类型）。
- `authority=(...)`：声明证据权威链，由 `aggregate.aggregate_by_authority`
  执行。链首来源是唯一比较基准，其余来源按各自的 `match` 窗口与基准比对；
  链首缺失时降级为人工复核，不让位给下一条来源，也不退化成投票。

声明本身位于业务扩展包 `forj_review_service.businesses.packs`；本模块只把它转成运行时结构。
新增一个需要权威关系的字段时，在扩展包里加一条 `authority` 即可，不需要
再写专用的裁决函数——`old_vehicle.vin` 原先就是这样一个约 130 行的函数。
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.packs import BUSINESS_PACKS
from forj_review_service.businesses.packs.model import (
    AuthorityRule,
    BusinessExtensionPack,
    EvidenceMode,
)


@dataclass(frozen=True)
class FieldEvidencePolicy:
    field: str
    mode: EvidenceMode
    allowed_document_types: tuple[str, ...] = ()
    allow_single_evidence: bool = True
    uncertain_requires_review: bool = True
    page_evidence_required: bool = True
    normalizer: str = "default"
    authority: tuple[AuthorityRule, ...] = ()
    # 日期时效：页面日期必须落在审核日往前推 N 天内。None 表示不检查。
    max_age_days: int | None = None


def _build_policies(pack: BusinessExtensionPack) -> dict[str, FieldEvidencePolicy]:
    policies = {
        item.key: FieldEvidencePolicy(
            field=item.key,
            mode=item.mode,
            allowed_document_types=item.sources,
            allow_single_evidence=item.allow_single_evidence,
            uncertain_requires_review=item.uncertain_requires_review,
            page_evidence_required=item.page_evidence_required and item.mode != "DERIVED",
            normalizer=item.normalizer,
            authority=item.authority,
            max_age_days=item.max_age_days,
        )
        for item in pack.fields
        if item.mode is not None
    }
    # 共用证据策略的页面字段（如 OCR 车架号）在材料字段之后克隆，避免依赖声明顺序。
    for item in pack.fields:
        if item.material_field is not None:
            policies[item.key] = replace(policies[item.material_field], field=item.key)
    return policies


def _merge_policies(
    packs: tuple[BusinessExtensionPack, ...],
) -> tuple[dict[str, FieldEvidencePolicy], dict[str, str]]:
    """把所有业务的证据策略合成一张表，字段键重复即启动期报错。

    字段键带业务分区前缀（`old_vehicle.` / `vehicle.`），各业务之间不重叠；
    这里显式校验，避免后者静默覆盖前者——那类问题只有在生产上才会表现为
    “字段按错误的来源策略比对”。
    """
    policies: dict[str, FieldEvidencePolicy] = {}
    owners: dict[str, str] = {}
    for pack in packs:
        for key, policy in _build_policies(pack).items():
            previous = owners.get(key)
            if previous is not None and previous != pack.business_type:
                raise ValueError(
                    f"字段 {key} 同时被 {previous} 和 {pack.business_type} 声明了证据策略"
                )
            policies[key] = policy
            owners[key] = pack.business_type
    material_fields = {
        item.key: item.material_field
        for pack in packs
        for item in pack.fields
        if item.material_field is not None
    }
    return policies, material_fields


FIELD_EVIDENCE_POLICIES, MATERIAL_FIELD_BY_PAGE_FIELD = _merge_policies(
    tuple(BUSINESS_PACKS.values())
)


def field_policy(
    field: str, *, business: BusinessContext = DEFAULT_BUSINESS
) -> FieldEvidencePolicy | None:
    return business.field_policies.get(field)


def date_window_problem(
    label: str,
    field: str,
    value: object,
    *,
    today: date | None = None,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> str | None:
    """按业务声明检查日期时效；国内审核日期不依赖部署主机的时区。"""
    from forj_review_service.evidence.fields.normalize import normalize_value

    policy = field_policy(field, business=business)
    if policy is None or not policy.max_age_days:
        return None
    normalized = normalize_value(field, value, business=business)
    if not normalized:
        return None  # 缺少值由证据聚合负责。
    try:
        parsed = date.fromisoformat(normalized)
    except ValueError:
        return f"{label}日期格式无法确认，请人工复核"
    review_day = today or datetime.now(timezone(timedelta(hours=8))).date()
    earliest = review_day - timedelta(days=policy.max_age_days)
    if earliest <= parsed <= review_day:
        return None
    return (
        f"{label} {parsed.isoformat()} 不在最近 {policy.max_age_days} 天内"
        f"（{earliest.isoformat()} 至 {review_day.isoformat()}），请人工复核"
    )


def filter_allowed_observations(
    field: str,
    observations,
    *,
    capability_sources=frozenset(),
    business: BusinessContext = DEFAULT_BUSINESS,
):
    """按实际材料类型过滤证据；未知类型不冒充允许来源。"""
    observations = [
        item
        for item in observations
        if item.source_type != "capability" or item.source_id in capability_sources
    ]
    policy = field_policy(field, business=business)
    if policy is None or policy.mode in {"SYSTEM", "DERIVED", "PAGE_AUXILIARY"}:
        return list(observations)
    allowed = set(policy.allowed_document_types)
    result = []
    for item in observations:
        if item.source_type == "capability" and item.source_id in capability_sources:
            result.append(item)
            continue
        if (
            item.source_type == "page"
            or item.source_type in {"image", "qr_page"}
            and (not item.document_type or item.document_type in allowed)
        ):
            result.append(item)
    return result

"""Read-only protocol and page catalog endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from forj_review_assistant.businesses.configuration.schema import PageDefinition
from forj_review_assistant.businesses.packs import build_collect_manifest
from forj_review_assistant.businesses.page_catalog import page_catalog
from forj_review_assistant.businesses.rules.catalog import RULE_DEFINITIONS
from forj_review_assistant.contracts.review import BusinessId
from forj_review_assistant.contracts.review_schema import review_contract_schema
from forj_review_assistant.interfaces.http.dependencies import get_review_service
from forj_review_assistant.runtime.review_service import ReviewService

router = APIRouter()
ReviewServiceDependency = Annotated[ReviewService, Depends(get_review_service)]


@router.get("/health")
def health() -> dict[str, str]:
    """供扩展和部署探针检查 Agent 是否已启动。"""
    return {"status": "ok"}


@router.get("/api/review/schema")
def review_schema() -> dict[str, object]:
    """Expose the versioned request/response contract to integration clients."""
    return review_contract_schema()


@router.get("/api/review/collect-manifest")
def collect_manifest(
    business_type: BusinessId,
    service: ReviewServiceDependency,
    region: str | None = None,
    profile_version: str | None = None,
    page_id: str | None = None,
) -> dict[str, object]:
    """下发页面采集清单：字段别名、图片分组标题和材料分组关键词。

    采集清单通常按业务包共用；传入地区、版本或 page_id 时会精确解析对应 Profile，
    让同业务多页面不会拿错清单。尚未声明清单的业务返回 404，前端退回内置表。
    """
    precise = region is not None or profile_version is not None or page_id is not None
    candidates = [
        p
        for p in (service.registry.profiles if precise else service.registry.active_profiles)
        if p.business_type == business_type
        and (region is None or p.region == region)
        and (profile_version is None or p.version == profile_version)
        and (page_id is None or not page_id or p.page_id == page_id)
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail="该业务地区版本不存在")
    if len(candidates) > 1:
        # Legacy regions sharing one declaration may still use business-only
        # requests. Distinct page packs require explicit page/version identity.
        if any(p.pack_declaration is not candidates[0].pack_declaration for p in candidates[1:]):
            raise HTTPException(
                status_code=409, detail="存在多个页面配置，请指定 page_id、地区和版本"
            )
    profile = candidates[0]
    pack = profile.pack_declaration
    if pack is None:
        raise HTTPException(status_code=404, detail="该业务尚未声明采集清单")
    result = build_collect_manifest(pack)
    if precise:
        result.update(
            page_id=profile.page_id, region=str(profile.region), profile_version=profile.version
        )
    return result


@router.get("/api/review/page-catalog")
def review_page_catalog(service: ReviewServiceDependency) -> dict[str, object]:
    """下发页面识别清单：管理端审核页地址 → 业务、地区、页面特征文案。

    必须独立于采集清单：识别发生在「按业务类型取采集清单」之前，识别不出来
    就不知道该取哪份清单。因此这里一次下发全部业务，而不是按业务类型查。
    """
    return page_catalog(service.registry.active_profiles)


@router.get("/api/review/configuration/schema")
def page_configuration_schema() -> dict[str, object]:
    """Machine-readable authoring contract; does not publish or execute changes."""
    return PageDefinition.model_json_schema()


@router.get("/api/review/configuration/rules")
def configuration_rules() -> dict[str, object]:
    """Registered rule metadata for configuration authors; no executable code."""
    return {
        "rules": [
            {
                "id": key,
                "required_fields": list(value.fields),
                "check_ids": list(value.checks),
                "stage": value.stage,
            }
            for key, value in sorted(RULE_DEFINITIONS.items())
        ]
    }

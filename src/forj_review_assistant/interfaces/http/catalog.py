"""Read-only protocol and page catalog endpoints."""

from fastapi import APIRouter, HTTPException

from forj_review_assistant.businesses.packs import (
    build_collect_manifest,
    pack_for_business,
)
from forj_review_assistant.businesses.page_catalog import page_catalog
from forj_review_assistant.contracts.review import BusinessType
from forj_review_assistant.contracts.review_schema import review_contract_schema

router = APIRouter()


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
    business_type: BusinessType,
    region: str | None = None,
    profile_version: str | None = None,
    page_id: str | None = None,
) -> dict[str, object]:
    """下发页面采集清单：字段别名、图片分组标题和材料分组关键词。

    采集清单通常按业务包共用；传入地区、版本或 page_id 时会精确解析对应 Profile，
    让同业务多页面不会拿错清单。尚未声明清单的业务返回 404，前端退回内置表。
    """
    pack = pack_for_business(business_type)
    if region is not None or profile_version is not None or page_id is not None:
        from forj_review_assistant.businesses.profiles import BUSINESS_PROFILES

        candidates = [
            p
            for p in BUSINESS_PROFILES
            if p.business_type == business_type
            and (region is None or p.region.value == region)
            and (profile_version is None or p.version == profile_version)
            and (page_id is None or not page_id or p.page_id == page_id)
        ]
        if not candidates:
            raise HTTPException(status_code=404, detail="该业务地区版本不存在")
        profile = candidates[-1]
        pack = profile.pack_declaration
    if pack is None:
        raise HTTPException(status_code=404, detail="该业务尚未声明采集清单")
    result = build_collect_manifest(pack)
    if region is not None or profile_version is not None or page_id is not None:
        result.update(region=profile.region.value, profile_version=profile.version)
    return result


@router.get("/api/review/page-catalog")
def review_page_catalog() -> dict[str, object]:
    """下发页面识别清单：管理端审核页地址 → 业务、地区、页面特征文案。

    必须独立于采集清单：识别发生在「按业务类型取采集清单」之前，识别不出来
    就不知道该取哪份清单。因此这里一次下发全部业务，而不是按业务类型查。
    """
    return page_catalog()

"""业务材料要求和受控执行预算。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SourceSelector:
    source_type: str
    document_type: str | None = None
    business_scope: str | None = None
    required_page: int | None = None
    source_field: str | None = None
    group_order: int | None = None


@dataclass(frozen=True)
class FieldSourceRequirement:
    field: str
    all_of: tuple[SourceSelector, ...] = ()
    any_of_groups: tuple[tuple[SourceSelector, ...], ...] = ()


@dataclass(frozen=True)
class MaterialRequirement:
    document_type: str
    business_scope: str
    minimum_count: int = 1
    required_pages: tuple[int, ...] = ()
    display_name: str = "审核材料"
    # 同组材料互为替代：组内任一材料满足要求时，其余成员不再报缺失。
    # 空字符串表示该材料不参与替代关系。车源审核的登记证书与车辆铭牌
    # 属于同一组——两件材料只需要上传其中一件。
    alternative_group: str = ""


@dataclass(frozen=True)
class RetryPolicy:
    semantic_retry_count: int = 1
    confidence_threshold: float = 0.70
    minimum_retry_window_seconds: float = 2.0
    qr_web_retry_count: int = 1


@dataclass(frozen=True)
class MaterialPolicy:
    mode: Literal["disabled", "observe", "enforce"]
    materials: tuple[MaterialRequirement, ...] = ()
    field_sources: tuple[FieldSourceRequirement, ...] = ()


DEFAULT_RETRY_POLICY = RetryPolicy()

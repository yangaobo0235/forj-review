"""从业务扩展包汇总页面字段的展示名称。"""

from forj_review_service.businesses.packs import BUSINESS_PACKS


def _merge_labels() -> dict[str, str]:
    """所有业务的字段键 → 中文标签。字段键带分区前缀，跨业务不重叠。"""
    merged: dict[str, str] = {}
    for business_type, pack in BUSINESS_PACKS.items():
        for key, label in pack.labels().items():
            if key in merged:
                raise ValueError(f"字段 {key} 被多个业务声明了标签：{business_type}")
            merged[key] = label
    return merged


# 页面字段的中文标签；任务展示和材料异常说明都用这一份。

PAGE_FIELD_LABELS = _merge_labels()

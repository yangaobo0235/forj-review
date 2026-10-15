"""各审核业务的主要字段清单及展示名称。

清单与标签的唯一来源是业务扩展包 `app.businesses.packs`；本模块只做导出
和分组，不再单独维护一份。报废置换的分组导出保持原样（页面写回和历史测试
依赖它的精确顺序），跨业务的标签表另建一张。
"""

from app.businesses.packs import BUSINESS_PACKS
from app.businesses.packs import SCRAP_REPLACEMENT_PACK as _PACK

_SECTION_KEYS = _PACK.section_field_keys()

# 分区 → 必审字段，顺序与扩展包中的声明顺序一致。
OLD_VEHICLE_FIELDS = _SECTION_KEYS["old_vehicle"]
NEW_VEHICLE_AND_INVOICE_FIELDS = _SECTION_KEYS["new_vehicle"]
PRIMARY_REVIEW_FIELDS = _PACK.required_keys()

# 报废置换页面控件的已知语义。实际字段数量和顺序来自每次 DOM 采集，
# 不能用本表推导页面字段总数。
SCRAP_PAGE_FIELD_LABELS = _PACK.labels()

# 规则检查项到页面字段的投影由扩展包的 `field_check_bindings` 声明，
# 不在这里维护第二份表：客户名称由主体关系辅助检查投影而来。


def _merge_labels() -> dict[str, str]:
    """所有业务的字段键 → 中文标签。字段键带分区前缀，跨业务不重叠。"""
    merged: dict[str, str] = {}
    for business_type, pack in BUSINESS_PACKS.items():
        for key, label in pack.labels().items():
            if key in merged:
                raise ValueError(f"字段 {key} 被多个业务声明了标签：{business_type.value}")
            merged[key] = label
    return merged


# 页面字段的中文标签；任务展示和材料异常说明都用这一份。
from app.businesses.context import ContextMapping

PAGE_FIELD_LABELS = ContextMapping(_merge_labels(), lambda pack: pack.labels())

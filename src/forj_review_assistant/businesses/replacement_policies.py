"""地区置换政策的数据结构。

政策数据本身位于各业务的扩展包（`forj_review_assistant.businesses.packs`）；本模块只定义结构，
供扩展包声明和规则执行使用。
"""

from dataclasses import dataclass
from datetime import date

from forj_review_assistant.contracts.review import Region


@dataclass(frozen=True)
class ReplacementPolicy:
    policy_id: str
    region: Region
    version: str
    invoice_date_from: date
    invoice_date_to: date
    disposal_deadline: date
    # 报废交车日期的下界。None 表示只卡截止日（历史口径：交早了不管）。
    # 字段排在 `disposal_deadline` 之后是因为它有默认值——数据类的必填项
    # 不能跟在带默认值的字段后面。
    disposal_date_from: date | None = None
    allowed_origins: tuple[str, ...] = ()
    origin_keywords: tuple[str, ...] = ()
    # 报废车辆类型档位复核：材料原文命中这些关键词时，该字段转人工复核。
    # 空元组表示本地区不做这条核验。
    vehicle_type_review_keywords: tuple[str, ...] = ()

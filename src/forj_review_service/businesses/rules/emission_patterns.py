"""完整型号查表未命中时使用的系列规则；只维护国三至国六的明确标记。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from forj_review_service.contracts.engine_emissions import engine_model_key

_STANDARD_BY_DIGIT = {"3": "国三", "4": "国四", "5": "国五", "6": "国六"}


@dataclass(frozen=True)
class EngineEmissionPattern:
    rule_id: str
    family: str
    prefix: str
    marker: str


@dataclass(frozen=True)
class EngineStandardCandidate:
    rule_id: str
    family: str
    standard: str
    marker: str


# 数字后界限避免把 E61、S50、Q40 等未维护编码截成 E6、S5、Q4。
# 车辆品牌不是发动机厂家，不参与系列判定。
ENGINE_EMISSION_PATTERNS = (
    EngineEmissionPattern("ca_e", "锡柴/大柴 CA", r"CA[A-Z0-9]", r"E([3-6])(?![0-9])"),
    EngineEmissionPattern("xichai_e", "锡柴 4DW/4DX", r"4D[WX][A-Z0-9]",
                          r"E([3-6])(?![0-9])"),
    EngineEmissionPattern("wp_e", "潍柴 WP", r"WP[A-Z0-9]", r"E([3-6])(?![0-9])"),
    EngineEmissionPattern("cummins_s", "康明斯 ISF/ISB/ISD/ISG", r"IS[FBDG]",
                          r"S([3-6])(?![0-9])"),
    EngineEmissionPattern("sc_q", "上柴 SC", r"SC[A-Z0-9]", r"Q([3-6])(?![0-9])"),
    EngineEmissionPattern("jx_q", "江铃 JX493ZL", r"JX493ZL", r"Q([3-6])(?![0-9])"),
    EngineEmissionPattern("yz_dash", "扬柴 YZ", r"YZ[A-Z0-9]", r"-([3-6])0(?=[^0-9]|$)"),
    EngineEmissionPattern("yc_dash", "玉柴 YC（含 YCS/YCK/YCY）", r"YC[A-Z0-9]",
                          r"-([3-6])0(?=[^0-9]|$)"),
    EngineEmissionPattern("united_dash", "联合动力 6K", r"6K[0-9]",
                          r"-([3-6])0(?=[^0-9]|$)"),
)


def infer_engine_emission(model: object) -> EngineStandardCandidate | None:
    """按型号自身系列推导；标记缺失或同一规则出现不同国几时不返回值。"""
    normalized = engine_model_key(model)
    # 无燃料字段时也排除型号中明确出现的天然气编码，避免套用柴油规则。
    if any(token in normalized for token in ("NG", "CNG", "LNG")):
        return None
    for rule in ENGINE_EMISSION_PATTERNS:
        if not re.match(rule.prefix, normalized):
            continue
        matches = list(re.finditer(rule.marker, normalized))
        digits = {match.group(1) for match in matches}
        if len(digits) != 1:
            return None
        return EngineStandardCandidate(
            rule_id=rule.rule_id,
            family=rule.family,
            standard=_STANDARD_BY_DIGIT[digits.pop()],
            marker=matches[0].group(0),
        )
    return None

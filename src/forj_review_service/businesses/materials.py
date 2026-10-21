"""文档识别策略与受限提示词构建。

主要职责：集中定义各类材料允许提取的字段，并根据业务范围生成字段白名单和识别提示词。
本模块只约束模型提取内容，不负责跨材料比对或生成最终审核结论。
"""

import json
from dataclasses import dataclass

from forj_review_service.businesses.context import DEFAULT_BUSINESS, BusinessContext
from forj_review_service.businesses.packs.model import MaterialDeclaration
from forj_review_service.contracts.recognition import RETRYABLE_UNCERTAIN_PREFIX

# 未识别出资料类型时的兜底类型；分类提示词的候选表按字典序输出。
UNSUPPORTED_DOCUMENT_TYPE = "unsupported"

COLLECTION_TYPE_INSTRUCTION = (
    "uncertain_fields 必须是 JSON 字符串数组；没有不确定字段时输出 []，"
    '只有一个时也必须输出如 ["vehicle.vin"]，禁止输出字符串、对象或 null。'
    "evidence_regions 必须是 JSON 对象数组。"
)

LAYOUT_INSTRUCTION = (
    "图片可能横向、竖向、倒置、倾斜，或把证件正反面、登记证第1页和第2页拼在同一张图中；"
    "请先根据证件标题、表格和印刷文字方向确定正确阅读方向，再逐个区域读取。"
)

RETRY_INSTRUCTIONS = {
    "low_confidence": "请重新检查整张材料，重新确认资料类型和所有清晰可见的白名单字段。",
    "invalid_json": "严格只返回一个符合既定结构的 JSON 对象，不要添加解释或代码围栏。",
    "invalid_schema": (
        "上一次响应字段类型错误，请重新输出完整 JSON。"
        "uncertain_fields 必须是字符串数组，evidence_regions 必须是对象数组，fields 必须是对象。"
    ),
    "invalid_structure": "只返回唯一 JSON 对象，不要返回示例或多个候选结果。",
    "document_type_mismatch": "重新确认资料类型，并只按该资料类型的白名单提取字段。",
    "empty_supported_fields": "重新检查材料中清晰可见的白名单字段；不要猜测不可见内容。",
}


def _uncertain_field_instruction(retry_reason: str) -> str | None:
    """把定向重读原因转成提示词指令；不是定向重读时返回 None。"""
    if not retry_reason.startswith(RETRYABLE_UNCERTAIN_PREFIX):
        return None
    fields = retry_reason.removeprefix(RETRYABLE_UNCERTAIN_PREFIX)
    return (
        f"上一次识别把以下字段标记为不确定：{fields}。"
        "请结合 evidence_regions 回到这些字段在原图中的位置，确认阅读方向后再读一次该区域。"
        "确认清楚的字段写入 fields 并从 uncertain_fields 中移除；"
        "仍然无法看清的保留在 uncertain_fields，不要猜测。"
    )


def _append_retry_instruction(prompt: str, retry_reason: str | None) -> str:
    """仅追加预定义的安全纠错指令，不回传模型上一次响应。"""
    if retry_reason:
        targeted = _uncertain_field_instruction(retry_reason)
        if targeted:
            return f"{prompt}{targeted}"
    instruction = RETRY_INSTRUCTIONS.get(retry_reason or "")
    return f"{prompt}{instruction}" if instruction else prompt


@dataclass(frozen=True)
class DocumentPolicy:
    """定义单类材料的提取白名单及其识别指引。

    ``fields`` 使用审核领域模型中的标准字段路径；模型只能返回白名单内的字段。
    ``scoped_fields`` 与 ``scoped_guidance`` 按业务范围覆写默认白名单和指引。
    ``field_guidance`` 说明该材料的读取位置和易混淆内容，不包含审核通过或驳回规则。
    """

    document_type: str
    display_name: str
    fields: tuple[str, ...]
    field_guidance: str
    scoped_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    scoped_guidance: tuple[tuple[str, str], ...] = ()
    scoped_names: tuple[tuple[str, str], ...] = ()
    # 票面只有一个号码的材料：`(号码字段, 派生字段)`，见 `MaterialDeclaration`。
    derived_field: tuple[str, str] | None = None
    allowed_scopes: tuple[str, ...] = ()

    def fields_for_scope(self, business_scope: str = "unknown") -> tuple[str, ...]:
        """返回当前业务范围允许提取的字段，未知业务使用材料默认白名单。"""
        if (
            self.allowed_scopes
            and business_scope != "unknown"
            and business_scope not in self.allowed_scopes
        ):
            return ()
        for key, fields in self.scoped_fields:
            if key == business_scope:
                return fields
        return self.fields

    def guidance_for_scope(self, business_scope: str) -> str:
        """返回匹配业务范围的材料识别指引。"""
        for key, guidance in self.scoped_guidance:
            if key == business_scope:
                return guidance
        return self.field_guidance

    def name_for_scope(self, business_scope: str) -> str:
        """返回匹配业务范围的材料称呼。"""
        for key, name in self.scoped_names:
            if key == business_scope:
                return name
        return self.display_name

    def build_extraction_prompt(
        self,
        image_index: int,
        business_scope: str = "unknown",
        *,
        retry_reason: str | None = None,
    ) -> str:
        """构建已知材料类型的提取提示词，并固定字段白名单和证据图片序号。"""
        allowed_fields = json.dumps(
            self.fields_for_scope(business_scope),
            ensure_ascii=False,
        )
        prompt = (
            f"你正在识别{self.name_for_scope(business_scope)}。"
            f"{LAYOUT_INSTRUCTION}{self.guidance_for_scope(business_scope)}"
            f"仅允许输出字段：{allowed_fields}。"
            "只读取图片中明确可见的内容，不推测、不补全；看不清的字段不写入 fields，"
            "并将字段名写入 uncertain_fields；版面不存在的字段不要写入 uncertain_fields，"
            "不适用的字段也不要写入 uncertain_fields。"
            "fields 是‘标准字段路径: 图片中的实际文字值’的对象，字段路径只能作为键，绝不能作为值；"
            "普通字段值保留图片中的原始文字和符号，不要替换同义词、修正易混淆字符或执行业务归类；"
            "页码数组和转移登记记录等明确要求的结构化字段按对应指引输出。"
            "只输出 JSON 对象，不使用 Markdown，不给出审核结论。"
            f'document_type 必须为 "{self.document_type}"。'
            "JSON 必须包含 document_type、fields、confidence、evidence_regions、uncertain_fields、qr_codes。"
            "qr_codes 仅填写当前图片中可靠读取的二维码原始内容，无法可靠读取则为 []；禁止拼接、推测或补全网址。"
            f"{COLLECTION_TYPE_INSTRUCTION}"
            f'evidence_regions 的每一项使用 field、image_index={image_index} 和 "box"，'
            "box 格式为 [x1, y1, x2, y2]。"
        )
        return _append_retry_instruction(prompt, retry_reason)


def _keep_order(merged: list[str], extra: tuple[str, ...]) -> tuple[str, ...]:
    """按首次出现顺序合并白名单，去掉重复项。"""
    for field in extra:
        if field not in merged:
            merged.append(field)
    return tuple(merged)


def _merge_scoped(left, right, combine):
    """按业务分区合并 scoped 覆写；同一分区出现在多份声明里时调用 combine。"""
    merged = list(left)
    for key, value in right:
        for index, (existing_key, existing_value) in enumerate(merged):
            if existing_key == key:
                merged[index] = (key, combine(existing_value, value))
                break
        else:
            merged.append((key, value))
    return tuple(merged)


def merge_material(
    declaration: MaterialDeclaration,
    current: DocumentPolicy | None,
) -> DocumentPolicy:
    """把一份材料声明并入已有策略；同一 document_type 被多个业务共用时叠加。

    白名单按分区叠加是安全的：材料提取按业务分区选择白名单，而各业务的分区
    互不重叠，所以叠加出来的分区表只会命中当前业务那一份。未声明分区的兜底
    白名单同样叠加——它只在业务分区不匹配时生效（如 classify 阶段）。
    """
    projected = DocumentPolicy(
        document_type=declaration.document_type,
        display_name=declaration.display_name,
        fields=declaration.fields,
        field_guidance=declaration.guidance,
        scoped_fields=declaration.scoped_fields
        or tuple((scope, declaration.fields) for scope in declaration.allowed_scopes),
        scoped_guidance=declaration.scoped_guidance
        or tuple((scope, declaration.guidance) for scope in declaration.allowed_scopes),
        scoped_names=declaration.scoped_names
        or tuple((scope, declaration.display_name) for scope in declaration.allowed_scopes),
        derived_field=declaration.derived_field,
        allowed_scopes=declaration.allowed_scopes,
    )
    if current is None:
        return projected
    return DocumentPolicy(
        document_type=current.document_type,
        # 首份声明的材料名作为兜底；分区分歧由 scoped_names 解决。
        display_name=current.display_name,
        fields=_keep_order(list(current.fields), projected.fields),
        field_guidance=f"{current.field_guidance}{projected.field_guidance}",
        scoped_fields=_merge_scoped(current.scoped_fields, projected.scoped_fields, _keep_order),
        scoped_guidance=_merge_scoped(
            current.scoped_guidance,
            projected.scoped_guidance,
            lambda left, right: f"{left}{right}",
        ),
        scoped_names=_merge_scoped(
            current.scoped_names, projected.scoped_names, lambda left, right: str(left)
        ),
        # 派生关系不属于任何分区：同一份材料在哪个业务分区里都是同一个票面号码。
        # 先声明的生效，两个业务对同一材料给出不同派生关系是配置错误。
        derived_field=current.derived_field or projected.derived_field,
        allowed_scopes=tuple(dict.fromkeys((*current.allowed_scopes, *projected.allowed_scopes)))
        if current.allowed_scopes and projected.allowed_scopes
        else (),
    )


def document_type_choices(*, business: BusinessContext = DEFAULT_BUSINESS):
    return "、".join([*sorted(business.document_policies), UNSUPPORTED_DOCUMENT_TYPE])


def build_classification_prompt(*, business: BusinessContext = DEFAULT_BUSINESS) -> str:
    """构建未知图片的首阶段分类提示词，不允许同时提取业务字段。"""
    return (
        f"只判断这张图片的资料类型。document_type 只能是 {document_type_choices(business=business)}。"
        "不要提取业务字段，不要输出身份证号码等敏感信息，不要给出审核结论。"
        "只输出 JSON 对象，包含 document_type、confidence、reason、identity_sides；confidence 取值为 0 到 1。"
        "身份证另外判断可确认的面别：人像面为 FRONT、国徽面为 BACK，identity_sides 为对应值的数组。"
        "同图清楚展示两面时可同时填写；面别看不清或非身份证时填写 []，禁止猜测。"
    )


def build_unknown_extraction_prompt(
    image_index: int,
    business_scope: str = "unknown",
    *,
    retry_reason: str | None = None,
    business: BusinessContext = DEFAULT_BUSINESS,
) -> str:
    """构建未知材料的一轮分类与提取提示词，并按业务范围限制候选字段。"""
    # 把每类材料的白名单与指引一并提供给模型，分类后只能输出命中类型对应的字段。
    policies = {
        key: {
            "name": policy.name_for_scope(business_scope),
            "fields": policy.fields_for_scope(business_scope),
            "guidance": policy.guidance_for_scope(business_scope),
        }
        for key, policy in business.document_policies.items()
        if policy.fields_for_scope(business_scope)
    }
    prompt = (
        f"请判断资料类型并提取图片中明确可见的审核字段。{LAYOUT_INSTRUCTION}"
        f"document_type 只能是 {document_type_choices(business=business)}。"
        "旧车、新车和车源车辆都是页面业务归属，不是图片资料类型。"
        f"各类型字段白名单：{json.dumps(policies, ensure_ascii=False)}。"
        "只能输出最终 document_type 对应白名单中的字段，不推测、不补全，不给出审核结论。"
        "fields 是‘标准字段路径: 图片中的实际文字值’的对象，字段路径只能作为键，绝不能作为值；"
        "普通字段值保留图片中的原始文字和符号，不自行修正、归类或补全。"
        "看不清的字段写入 uncertain_fields；版面不存在的字段不要写入 uncertain_fields，"
        "不适用的字段也不要写入 uncertain_fields。"
        "只输出 JSON 对象，包含 document_type、fields、confidence、evidence_regions、uncertain_fields、qr_codes。"
        "qr_codes 仅填写当前图片中可靠读取的二维码原始内容，无法可靠读取则为 []；禁止拼接、推测或补全网址。"
        f"{COLLECTION_TYPE_INSTRUCTION}"
        f'evidence_regions 使用 image_index={image_index} 和 "box"=[x1,y1,x2,y2]。'
    )
    return _append_retry_instruction(prompt, retry_reason)

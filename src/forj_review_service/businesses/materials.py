"""文档识别策略与字段白名单。

主要职责：按业务范围提供材料名称、字段映射白名单和读取指引。
本模块不负责跨材料比对或生成最终审核结论。
"""

from dataclasses import dataclass

from forj_review_service.businesses.packs.model import MaterialDeclaration


@dataclass(frozen=True)
class DocumentPolicy:
    """定义单类材料的提取白名单及其识别指引。

    ``fields`` 使用审核领域模型中的标准字段路径；标准映射只返回白名单内的字段。
    ``raw_fields`` 独立记录票面标签、区域与原文，不自动成为比较证据。
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
    白名单同样叠加，用于尚未匹配到具体分区的识别输入；最终路由仍校验业务归属。
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

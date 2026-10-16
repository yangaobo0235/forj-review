"""页面识别声明：管理端审核页 → 业务与地区。

报废置换沿用路径识别。车源、过户、一致性审核共用一致性入口，浏览器按
声明的 review_tab 核对当前激活页签和对应面板，不能用整页文字推断步骤。

这里的声明是浏览器识别表的唯一来源（另有扩展包里的 `admin_paths` 与
`page_anchors`、`review_tab` 会一并汇总进来）。浏览器不再自己维护路径表：路径表写死在
浏览器里的后果是，新增一个业务要改前端代码，而且改漏不会报错，只会静默
认成别的业务。
"""

from __future__ import annotations

from dataclasses import dataclass

from forj_review_assistant.contracts.review import (
    BusinessId,
    BusinessType,
    Region,
    RegionId,
)

# 保留历史特征元数据；声明 review_tab 后浏览器不再使用整页文字判定步骤。
CONSISTENCY_ANCHORS = ("新车信息", "车辆所有人类型", "新车挂靠")

# 车源和过户已有扩展包，地址与审核页签从活动 Profile 的地区声明汇总。


@dataclass(frozen=True)
class PageIdentity:
    """一个审核页面：地址、可选审核页签及它所属的业务和地区。"""

    business_type: BusinessId
    region: RegionId
    paths: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    profile_version: str = "1.0"
    title: str = ""
    page_id: str = ""
    review_tab: str = ""


# 车源、过户、一致性共用的入口地址。青岛和长春两套页面使用同一套规则，
# 业务本身不分地区，所以两个地址挂在同一条声明上、取默认地区——和车源审核
# 同一个口径。地区维度只在报废置换里有意义（两地政策不同）。
CONSISTENCY_TRANSFER_PATHS = ("/consistency-qingdao", "/consistency-changchun")

# 尚未声明扩展包的业务保留显式声明（和 `profiles.py` 里的未配置 Profile 对应）。
# 过户审核已有扩展包，它的地址和特征由 `TRANSFER_PACK` 声明，不在这里重复。
_DECLARED_IDENTITIES: tuple[PageIdentity, ...] = (
    PageIdentity(
        BusinessType.CONSISTENCY,
        Region.DEFAULT,
        paths=CONSISTENCY_TRANSFER_PATHS,
        anchors=CONSISTENCY_ANCHORS,
        review_tab="一致性审核",
    ),
)


def active_identities(profiles=None):
    from forj_review_assistant.businesses.profiles import BUSINESS_PROFILES

    active = {}
    for profile in BUSINESS_PROFILES if profiles is None else profiles:
        active[profile.page_id or f"{profile.business_type}:{profile.region}"] = profile
    result = []
    for page_id, profile in active.items():
        key = (profile.business_type, profile.region)
        pack = profile.pack_declaration
        declaration = pack.region(profile.region) if pack else None
        if declaration:
            result.append(
                PageIdentity(
                    *key,
                    declaration.admin_paths,
                    declaration.page_anchors,
                    profile.version,
                    pack.title,
                    page_id,
                    declaration.review_tab,
                )
            )
        else:
            result.extend(
                item for item in _DECLARED_IDENTITIES if (item.business_type, item.region) == key
            )
    return tuple(result)


def path_matches(path: str, prefix: str) -> bool:
    """路径是否命中该前缀。按完整路径段匹配，避免相似前缀互相误判。"""
    return path == prefix or path.startswith(f"{prefix}/")


def identities_for_path(path: str, profiles=None) -> tuple[PageIdentity, ...]:
    """该地址可能属于哪些业务。地址唯一时不返回多条，地址共享时返回全部候选。

    审核请求校验业务、地区及 page_id 属于路径候选。共享一致性入口的三个业务
    都使用 default 地区，当前步骤由浏览器确认；报废置换仍校验对应地区。
    """
    return tuple(
        item
        for item in active_identities(profiles)
        if any(path_matches(path, route) for route in item.paths)
    )


def page_catalog(profiles=None) -> dict[str, object]:
    """下发给浏览器的页面识别清单。

    浏览器据此判定当前页面是哪个业务；清单不可用时浏览器退回内置表，但内置表
    对共享地址只返回「认不出来」，要求人工选择，不会猜。
    """
    return {
        "version": "1.0",
        "identities": [
            {
                "business_type": str(item.business_type),
                "region": str(item.region),
                **(
                    {"profile_version": item.profile_version}
                    if item.profile_version != "1.0"
                    else {}
                ),
                **({"title": item.title} if item.title else {}),
                **({"page_id": item.page_id} if item.page_id else {}),
                **({"review_tab": item.review_tab} if item.review_tab else {}),
                "paths": list(item.paths),
                "anchors": list(item.anchors),
            }
            for item in active_identities(profiles)
        ],
    }

"""页面识别声明：管理端审核页 → 业务与地区。

报废置换沿用路径识别；车源、过户、一致性使用激活页签与关联面板。
页面身份独立于审核规则，清空规则不会移除页面识别。

这里的声明是浏览器识别表的唯一来源（另有扩展包里的 `admin_paths` 与
`page_anchors`、`review_tab` 会一并汇总进来）。浏览器不再自己维护路径表：路径表写死在
浏览器里的后果是，新增一个业务要改前端代码，而且改漏不会报错，只会静默
认成别的业务。
"""

from __future__ import annotations

from dataclasses import dataclass

from forj_review_service.contracts.review import (
    BusinessId,
    BusinessType,
    Region,
    RegionId,
)

# 保留历史特征元数据；声明 review_tab 后浏览器不再使用整页文字判定步骤。
CONSISTENCY_ANCHORS = ("新车信息", "车辆所有人类型", "新车挂靠")

# 已配置页面的地址与审核页签从活动 Profile 的地区声明汇总。


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


# 一致性审核不分地区；两个城市入口使用同一份页面身份。
CONSISTENCY_PATHS = ("/consistency-qingdao", "/consistency-changchun")

# 尚无扩展包的审核步骤仍保留页面身份，不恢复其旧字段、材料或规则。
_DECLARED_IDENTITIES: tuple[PageIdentity, ...] = (
    PageIdentity(
        BusinessType.CONSISTENCY,
        Region.DEFAULT,
        paths=CONSISTENCY_PATHS,
        anchors=CONSISTENCY_ANCHORS,
        review_tab="一致性审核",
    ),
)


def active_identities(profiles=None):
    if profiles is None:
        from forj_review_service.businesses.profiles import CONSISTENCY_DEFAULT

        profiles = (CONSISTENCY_DEFAULT,)
    active = {}
    for profile in profiles:
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

    审核请求校验业务、地区及 page_id 属于路径候选。
    一致性审核的当前步骤由浏览器确认；报废置换仍校验对应地区。
    """
    return tuple(
        item
        for item in active_identities(profiles)
        if any(path_matches(path, route) for route in item.paths)
    )


def page_catalog(profiles=None) -> dict[str, object]:
    """下发给浏览器的页面识别清单。

    浏览器据此判定当前页面是哪个业务；清单不可用时使用同一声明生成的随包目录，
    共享地址仍按激活审核页签及对应面板确认，不按地址猜步骤。
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

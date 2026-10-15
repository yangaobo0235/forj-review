"""页面识别声明：管理端审核页 → 业务与地区。

审核页地址不再唯一对应一个业务。青岛一致性审核和青岛过户审核都挂在
`/consistency-qingdao` 下，靠列表页的状态筛选进到各自的页面，URL 完全一样；
长春同理。因此本模块给出的是**候选集合**，不是唯一答案，识别由浏览器执行——
拿页面上的特征文案在候选里筛，筛不出唯一一条就交给审核员选，绝不猜。

这里的声明是浏览器识别表的唯一来源（另有扩展包里的 `admin_paths` 与
`page_anchors` 会一并汇总进来）。浏览器不再自己维护路径表：路径表写死在
浏览器里的后果是，新增一个业务要改前端代码，而且改漏不会报错，只会静默
认成别的业务。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.businesses.packs import BUSINESS_PACKS
from app.models.review import BusinessType, Region

# 一致性审核页独有的详情区文字。
#
# **不能用「过户」两个字**：一致性审核页上有个页签就叫「过户详情」，而过户
# 审核页上反而没有——方向正好相反。也不能用开票日期、发票代码、发票号码、
# 开票金额、识别车架号、经销商这些：两个页面的发票字段区几乎一模一样，两边
# 都有。只能取区块标题和独有字段标签。
CONSISTENCY_ANCHORS = ("新车信息", "车辆所有人类型", "新车挂靠")

# 过户审核的特征不在这里：它已经有扩展包，地址和特征都由 `TRANSFER_PACK` 的
# `RegionDeclaration` 声明，再由 `_pack_identities()` 汇总进来。同一个地址挂
# 多个业务时，两边的特征必须互不重叠，由 `tests/test_page_catalog.py` 锁住。


@dataclass(frozen=True)
class PageIdentity:
    """一个审核页面：地址 + 页面特征 + 它属于哪个业务和地区。"""

    business_type: BusinessType
    region: Region
    paths: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()
    profile_version: str = "1.0"
    title: str = ""
    page_id: str = ""


def _pack_identities() -> tuple[PageIdentity, ...]:
    """已声明扩展包的业务：页面地址和特征都来自业务声明。"""
    return tuple(
        PageIdentity(
            business_type=BusinessType(pack.business_type),
            region=declaration.region,
            paths=declaration.admin_paths,
            anchors=declaration.page_anchors,
        )
        for pack in BUSINESS_PACKS.values()
        for declaration in pack.regions
    )


# 一致性审核与过户审核的页面地址。青岛和长春两套页面**用的是同一套规则**，
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
    ),
)

PAGE_IDENTITIES: tuple[PageIdentity, ...] = (*_pack_identities(), *_DECLARED_IDENTITIES)


def active_identities():
    from app.businesses.profiles import BUSINESS_PROFILES
    active = {}
    for profile in BUSINESS_PROFILES:
        active[profile.page_id or f"{profile.business_type.value}:{profile.region.value}"] = profile
    result = []
    for page_id, profile in active.items():
        key = (profile.business_type, profile.region)
        pack = profile.pack_declaration
        declaration = pack.region(profile.region) if pack else None
        if declaration:
            result.append(PageIdentity(*key, declaration.admin_paths, declaration.page_anchors, profile.version, pack.title, page_id))
        else:
            result.extend(item for item in _DECLARED_IDENTITIES if (item.business_type, item.region) == key)
    return tuple(result)


def path_matches(path: str, prefix: str) -> bool:
    """路径是否命中该前缀。按完整路径段匹配，避免相似前缀互相误判。"""
    return path == prefix or path.startswith(f"{prefix}/")


def identities_for_path(path: str) -> tuple[PageIdentity, ...]:
    """该地址可能属于哪些业务。地址唯一时不返回多条，地址共享时返回全部候选。

    审核请求的页面地址校验用它：地址共享意味着「地址」不能再作为业务判定的
    依据，但**地区**仍然可以——共享的两条声明地区相同，所以拿长春的业务去审
    青岛的单子照样被挡住。
    """
    return tuple(
        item for item in active_identities() if any(path_matches(path, route) for route in item.paths)
    )


def page_catalog() -> dict[str, object]:
    """下发给浏览器的页面识别清单。

    浏览器据此判定当前页面是哪个业务；清单不可用时浏览器退回内置表，但内置表
    对共享地址只返回「认不出来」，要求人工选择，不会猜。
    """
    return {
        "version": "1.0",
        "identities": [
            {
                "business_type": item.business_type.value,
                "region": item.region.value,
                **({"profile_version": item.profile_version} if item.profile_version != "1.0" else {}),
                **({"title": item.title} if item.title else {}),
                **({"page_id": item.page_id} if item.page_id else {}),
                "paths": list(item.paths),
                "anchors": list(item.anchors),
            }
            for item in active_identities()
        ],
    }

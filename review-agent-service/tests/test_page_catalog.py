"""页面识别声明：地址 → 业务、地区、页面特征。

背景：青岛一致性审核和青岛过户审核都挂在 `/consistency-qingdao` 下，长春同理。
地址不再唯一对应业务，识别要靠页面上的特征文案，所以这组用例锁定的就是
「共享地址的每一条声明都必须能靠特征被区分出来」。
"""

from collections import defaultdict

from app.businesses.page_catalog import (
    PAGE_IDENTITIES,
    identities_for_path,
    page_catalog,
    path_matches,
)
from app.models.review import BusinessType, Region


def test_shared_path_lists_both_businesses() -> None:
    """两个业务共用这两个地址，而且都不分地区——青岛和长春同一套规则。"""
    for path in ("/consistency-qingdao", "/consistency-changchun"):
        found = {(item.business_type, item.region) for item in identities_for_path(path)}
        assert found == {
            (BusinessType.CONSISTENCY, Region.DEFAULT),
            (BusinessType.TRANSFER, Region.DEFAULT),
        }, path


def test_unique_paths_look_the_same_as_before() -> None:
    """地址唯一的业务不受影响：识别照旧只认地址。"""
    cases = {
        "/scrap-replace-qingdao": (BusinessType.SCRAP_REPLACEMENT, Region.QINGDAO),
        "/scrap-replace-changchun": (BusinessType.SCRAP_REPLACEMENT, Region.CHANGCHUN),
        "/vehicle-source": (BusinessType.VEHICLE_SOURCE, Region.DEFAULT),
    }
    for path, expected in cases.items():
        found = [(item.business_type, item.region) for item in identities_for_path(path)]
        assert found == [expected], path


def test_every_shared_path_business_declares_anchors() -> None:
    """共享地址的业务必须声明特征，否则两条声明永远分不开。"""
    by_path: dict[str, list] = defaultdict(list)
    for item in PAGE_IDENTITIES:
        for path in item.paths:
            by_path[path].append(item)

    for path, items in by_path.items():
        if len(items) < 2:
            continue
        for item in items:
            assert item.anchors, f"{path} 上的 {item.business_type.value} 没有声明页面特征"


def test_anchors_of_businesses_sharing_a_path_do_not_overlap() -> None:
    """共用一个地址的两个业务不能有相同的特征词。

    有重叠就意味着某个词在两个页面上都出现，识别会变得依赖「命中了几个」，
    改版挪走一个词就能把结论翻过来，而且不报错。
    """
    by_path: dict[str, list] = defaultdict(list)
    for item in PAGE_IDENTITIES:
        for path in item.paths:
            by_path[path].append(item)

    for path, items in by_path.items():
        if len(items) < 2:
            continue
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                assert not set(left.anchors) & set(right.anchors), path


def test_anchors_avoid_words_the_whole_backend_shares() -> None:
    """特征不能取侧边栏、页签或审核按钮区的文字。

    后台是列表页不卸载、详情叠上去的结构，`document.body.innerText` 把菜单和
    列表一起收进来。取这些词当特征的话，两个业务会同时命中。
    """
    shared_words = ("一致性审核", "报废置换审核", "赋界", "审核处理", "立即提交", "取消")

    for item in PAGE_IDENTITIES:
        for anchor in item.anchors:
            assert not any(word in anchor for word in shared_words), anchor


def test_path_matching_uses_complete_segments() -> None:
    assert path_matches("/scrap-replace-qingdao", "/scrap-replace-qingdao")
    assert path_matches("/scrap-replace-qingdao/detail/1", "/scrap-replace-qingdao")
    assert not path_matches("/scrap-replace-qingdao-old", "/scrap-replace-qingdao")
    assert not path_matches("/consistency-qingdaox", "/consistency-qingdao")


def test_catalog_shape_matches_what_the_browser_reads() -> None:
    catalog = page_catalog()

    assert catalog["version"] == "1.0"
    identities = catalog["identities"]
    assert len(identities) == len(PAGE_IDENTITIES)
    for item in identities:
        assert {"business_type", "region", "paths", "anchors"} <= set(item) <= {
            "business_type", "region", "paths", "anchors", "title", "profile_version", "page_id"}
        if "title" in item:
            assert isinstance(item["title"], str) and item["title"]
        assert isinstance(item["paths"], list) and item["paths"]
        assert isinstance(item["anchors"], list)


def test_pack_declared_anchors_reach_the_catalog() -> None:
    """扩展包里的页面特征会汇总进清单，不另维护一份。"""
    vehicle_source = next(
        item for item in PAGE_IDENTITIES
        if item.business_type is BusinessType.VEHICLE_SOURCE
    )

    assert "车源审核" in vehicle_source.anchors

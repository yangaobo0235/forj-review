"""报废置换保留路径识别；其余三个业务声明各自的激活审核页签。"""

from collections import defaultdict

from forj_review_assistant.businesses.page_catalog import (
    active_identities,
    identities_for_path,
    page_catalog,
    path_matches,
)
from forj_review_assistant.contracts.review import BusinessType, Region


def test_shared_path_lists_three_businesses() -> None:
    """车源校验也在一致性入口内执行，三个业务均使用 default 规则。"""
    for path in ("/consistency-qingdao", "/consistency-changchun"):
        found = {(item.business_type, item.region) for item in identities_for_path(path)}
        assert found == {
            (BusinessType.CONSISTENCY, Region.DEFAULT),
            (BusinessType.TRANSFER, Region.DEFAULT),
            (BusinessType.VEHICLE_SOURCE, Region.DEFAULT),
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


def test_shared_path_businesses_declare_distinct_review_tabs() -> None:
    by_path: dict[str, list] = defaultdict(list)
    for item in active_identities():
        for path in item.paths:
            by_path[path].append(item)

    for path, items in by_path.items():
        if len(items) < 2:
            continue
        assert all(item.review_tab for item in items), path
        assert len({item.review_tab for item in items}) == len(items), path


def test_anchors_of_businesses_sharing_a_path_do_not_overlap() -> None:
    """共用一个地址的两个业务不能有相同的特征词。

    有重叠就意味着某个词在两个页面上都出现，识别会变得依赖「命中了几个」，
    改版挪走一个词就能把结论翻过来，而且不报错。
    """
    by_path: dict[str, list] = defaultdict(list)
    for item in active_identities():
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

    for item in active_identities():
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
    assert len(identities) == len(active_identities())
    for item in identities:
        assert {"business_type", "region", "paths", "anchors"} <= set(item) <= {
            "business_type", "region", "paths", "anchors", "title", "profile_version", "page_id", "review_tab"}
        if "title" in item:
            assert isinstance(item["title"], str) and item["title"]
        assert isinstance(item["paths"], list) and item["paths"]
        assert isinstance(item["anchors"], list)
        if item["business_type"] == "scrap_replacement":
            assert "review_tab" not in item
        else:
            assert item["review_tab"] in {"车源审核", "过户审核", "一致性审核"}


def test_pack_declared_anchors_reach_the_catalog() -> None:
    """扩展包里的页面特征会汇总进清单，不另维护一份。"""
    vehicle_source = next(
        item for item in active_identities()
        if item.business_type is BusinessType.VEHICLE_SOURCE
    )

    assert "车源审核" in vehicle_source.anchors

"""活动页面目录及独立配置的共享地址声明。"""

from forj_review_assistant.businesses.page_catalog import (
    active_identities,
    identities_for_path,
    page_catalog,
    path_matches,
)
from forj_review_assistant.contracts.review import BusinessType, Region


def test_bundled_extension_catalog_matches_backend_declarations():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    source = (root / "apps/extension/src/platform/browser/catalog/bundled-page-catalog.ts").read_text(encoding="utf-8")
    bundled = json.loads(source.split(" = ", 1)[1].strip().removesuffix(";"))
    assert bundled == page_catalog(), "运行 scripts/dev/export-page-catalog.py 更新识别目录"


def test_consistency_paths_keep_all_three_review_steps() -> None:
    """新车源与一致性使用激活页签区分。"""
    for path in ("/consistency-qingdao", "/consistency-changchun"):
        found = {(item.business_type, item.region) for item in identities_for_path(path)}
        assert found == {
            (BusinessType.CONSISTENCY, Region.DEFAULT),
            (BusinessType.VEHICLE_SOURCE, Region.DEFAULT),
            (BusinessType.TRANSFER, Region.DEFAULT),
        }, path


def test_unique_paths_look_the_same_as_before() -> None:
    """地址唯一的业务不受影响：识别照旧只认地址。"""
    cases = {
        "/scrap-replace-qingdao": (BusinessType.SCRAP_REPLACEMENT, Region.QINGDAO),
        "/scrap-replace-changchun": (BusinessType.SCRAP_REPLACEMENT, Region.CHANGCHUN),
    }
    for path, expected in cases.items():
        found = [(item.business_type, item.region) for item in identities_for_path(path)]
        assert found == [expected], path


def test_configured_pages_can_share_route_with_distinct_tabs() -> None:
    from forj_review_assistant.businesses.configuration.compiler import compile_page
    from forj_review_assistant.businesses.configuration.schema import PageDefinition

    profiles = tuple(compile_page(PageDefinition.model_validate({
        "page_id": key, "business_type": key, "title": title, "paths": ["/sample-review"],
        "review_tab": title, "scopes": [key],
        "materials": [{"document_type": "permit", "label": "许可证"}],
        "fields": [{"key": f"{key}.number", "label": "编号", "aliases": ["编号"],
                    "evidence": [{"material": "permit", "field": "printed.number"}]}],
    })) for key, title in (("initial", "初审"), ("final", "复审")))
    candidates = identities_for_path("/sample-review", profiles)
    assert {(item.business_type, item.review_tab) for item in candidates} == {
        ("initial", "初审"), ("final", "复审"),
    }
    assert len(page_catalog(profiles)["identities"]) == 2


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
            assert item["review_tab"] == {"consistency": "一致性审核", "vehicle_source": "车源审核", "transfer": "过户审核"}[item["business_type"]]


def test_scrap_pack_declared_anchors_reach_the_catalog() -> None:
    from forj_review_assistant.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
    declaration = SCRAP_REPLACEMENT_QINGDAO.pack_declaration.region(Region.QINGDAO)
    identity = next(item for item in active_identities()
                    if item.business_type == BusinessType.SCRAP_REPLACEMENT and item.region == Region.QINGDAO)
    assert identity.anchors == declaration.page_anchors

from dataclasses import replace

import pytest

from forj_review_assistant.businesses.context import business_context
from forj_review_assistant.businesses.packs.model import FieldDeclaration
from forj_review_assistant.businesses.profiles import SCRAP_REPLACEMENT_QINGDAO
from forj_review_assistant.contracts.review import FieldObservation, FieldStatus
from forj_review_assistant.evidence.comparison.aggregate import aggregate_field
from forj_review_assistant.evidence.fields.differences import value_differences
from forj_review_assistant.evidence.fields.normalize import normalize_value


@pytest.fixture
def engine_model_policy():
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, pack_declaration=replace(
        SCRAP_REPLACEMENT_QINGDAO.pack_declaration,
        fields=(FieldDeclaration("sample.engine_model", "发动机型号", normalizer="engine_model", mode="PARALLEL"),),
    ))
    with business_context(profile):
        yield


def test_detailed_tractor_type_matches_page_business_category() -> None:
    assert normalize_value("old_vehicle.type", "重型半挂牵引车") == "牵引车"
    assert normalize_value("old_vehicle.type", "牵引车") == "牵引车"


def test_detailed_cargo_type_matches_page_business_category() -> None:
    assert normalize_value("old_vehicle.type", "中型仓栅式货车") == "载货汽车"
    assert normalize_value("old_vehicle.type", "载货汽车") == "载货汽车"


def test_engine_model_compares_only_the_model_itself(engine_model_policy) -> None:
    """证件写“潍柴WP10.5H430E62”，页面可能只写型号；品牌不参与比较。

    品牌和型号是两件事，页面与材料各写各的；带不带品牌都算同一型号。
    """
    assert normalize_value("sample.engine_model", "潍柴WP10.5H430E62") == "WP10.5H430E62"
    assert normalize_value("sample.engine_model", "潍柴 WP10.5H430E62") == "WP10.5H430E62"
    assert normalize_value("sample.engine_model", "潍柴WP10.5H430E62") == normalize_value(
        "sample.engine_model", "WP10.5H430E62"
    )
    # 型号本身不同时仍然是冲突，不能被品牌归一化抹平。
    assert normalize_value("sample.engine_model", "潍柴WP10.5H430E62") != normalize_value(
        "sample.engine_model", "WP13NG480E61"
    )


def test_engine_model_comparison_ignores_the_brand_prefix(engine_model_policy) -> None:
    result = aggregate_field(
        "sample.engine_model",
        [
            FieldObservation(
                field="sample.engine_model",
                source_type="page",
                source_id="review_page",
                value="潍柴WP13NG480E61",
            ),
            FieldObservation(
                field="sample.engine_model",
                source_type="image",
                source_id="img-1",
                value="WP13NG480E61",
            ),
        ],
    )
    assert result.status is FieldStatus.MATCH


def test_engine_model_highlight_stays_on_the_model_not_the_brand(engine_model_policy) -> None:
    """品牌不参与比较，高亮也不能标出「缺少 潍柴」。"""
    assert value_differences("sample.engine_model", "潍柴WP13NG480E61", "WP13NG480E61") == []

    branded = value_differences("sample.engine_model", "潍柴WP10.5H430E62", "WP13NG480E61")
    plain = value_differences("sample.engine_model", "WP10.5H430E62", "WP13NG480E61")

    assert branded
    assert all("潍柴" not in item.page_text for item in branded)
    # 品牌被裁掉后，区间内容与不带品牌的页面值完全一致……
    assert [
        (item.kind, item.start, item.end, item.page_text) for item in branded
    ] == [(item.kind, item.start, item.end, item.page_text) for item in plain]
    # ……页面侧坐标整体后移两个字的宽度，标注仍然落在型号上。
    assert [item.page_start for item in branded] == [item.page_start + 2 for item in plain]
    assert [item.page_end for item in branded] == [item.page_end + 2 for item in plain]


def test_engine_model_preserves_leading_i_and_one_difference() -> None:
    assert normalize_value("old_vehicle.engine_model", "1SGe4-460") == "1SGE4460"
    assert normalize_value("old_vehicle.engine_model", "ISGe4-460") == "ISGE4460"

    result = aggregate_field(
        "old_vehicle.engine_model",
        [
            FieldObservation(
                field="old_vehicle.engine_model",
                source_type="image",
                source_id="img-1",
                value="1SGe4-460",
            ),
            FieldObservation(
                field="old_vehicle.engine_model",
                source_type="page",
                source_id="review_page",
                value="ISGe4-460",
            ),
        ],
    )
    assert result.status is FieldStatus.CONFLICT


def test_fuel_type_treats_electric_labels_as_new_energy() -> None:
    assert normalize_value("new_vehicle.fuel_type", "电") == "新能源"
    assert normalize_value("new_vehicle.fuel_type", "纯电动") == "新能源"
    assert normalize_value("new_vehicle.fuel_type", "新能源") == "新能源"


def test_fuel_type_keeps_hybrid_distinct_from_new_energy() -> None:
    assert normalize_value("new_vehicle.fuel_type", "油电混合") == "混合动力"
    assert normalize_value("new_vehicle.fuel_type", "混合动力") == "混合动力"
    assert normalize_value("new_vehicle.fuel_type", "电") != normalize_value(
        "new_vehicle.fuel_type", "混合动力"
    )

"""系列规则的范围、国三至国六标记及防止误截数字的边界。"""

import pytest

from forj_review_service.businesses.rules.emission_patterns import infer_engine_emission


@pytest.mark.parametrize(("digit", "standard"), [("3", "国三"), ("4", "国四"),
                                               ("5", "国五"), ("6", "国六")])
@pytest.mark.parametrize(("template", "rule_id"), [
    ("CA6DL1-28E{digit}F", "ca_e"),
    ("4DW93-84E{digit}", "xichai_e"),
    ("4DX23-110E{digit}", "xichai_e"),
    ("WP10H400E{digit}", "wp_e"),
    ("ISF3.8S{digit}R154", "cummins_s"),
    ("ISB3.9S{digit}R140", "cummins_s"),
    ("ISD180S{digit}A", "cummins_s"),
    ("ISG12S{digit}A", "cummins_s"),
    ("SC9DK240Q{digit}", "sc_q"),
    ("JX493ZLQ{digit}", "jx_q"),
    ("YZ4DA2-{digit}0", "yz_dash"),
    ("YC6K560-{digit}0A", "yc_dash"),
    ("YCS04-{digit}0", "yc_dash"),
    ("YCK05-{digit}0", "yc_dash"),
    ("YCY24-{digit}0", "yc_dash"),
    ("6K1346N-{digit}0", "united_dash"),
])
def test_each_scoped_series_supports_four_standards(template, rule_id, digit, standard):
    candidate = infer_engine_emission(template.format(digit=digit))
    assert candidate is not None
    assert candidate.standard == standard
    assert candidate.rule_id == rule_id


@pytest.mark.parametrize("model", [
    "WP2.3NQ130E61", "WP10E50", "SC9DK240Q40", "ISF3.8S50R154", "ISF3.8S5154",
    "YC6K560", "MC11.44-600", "CA4DF3", "CM6D28.38041",
    "D25TCIF1", "Q23-132E60", "ISGe5-400", "X12NS6B490",
    "YC6K560-61", "CA6E3E5", "SC9Q3Q4", "YC6-30A-50",
    "WP10E2", "CA6E7", "", None,
    "BF4M2012-16E4", "CM6D28.38040", "MC11.44-50", "JE493ZLQ5",
    "Q23-132E60", "JX4D25B6H", "WP10NG336E5", "OM501LA", "4G69S4N",
    "WP10.336", "YC6A260-33", "YZ4DA2-400", "6K1346N-600",
])
def test_unknown_encoding_and_conflicting_markers_have_no_candidate(model):
    assert infer_engine_emission(model) is None


def test_fallback_shares_exact_lookup_normalization():
    candidate = infer_engine_emission("上柴 ＳＣ９ＤＫ２４０Ｑ４")
    assert candidate is not None
    assert candidate.standard == "国四"
    assert candidate.marker == "Q4"

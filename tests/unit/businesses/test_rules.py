from forj_review_service.evidence.fields.normalize import normalize_value


def test_normalize_identifiers_removes_spaces_and_uppercases() -> None:
    assert normalize_value("old_vehicle.vin", " lf ws rx9 7l ") == "LFWSRX97L"


def test_normalize_owner_keeps_chinese_text_but_removes_spaces() -> None:
    assert normalize_value("old_vehicle.owner", " 李  明 ") == "李明"


def test_normalize_owner_removes_appended_unified_social_credit_code() -> None:
    value = "丹阳市双跃危险货物运输有限公司/统一社会信用代码/91321181730111164"

    assert normalize_value("old_vehicle.owner", value) == "丹阳市双跃危险货物运输有限公司"


def test_normalize_owner_removes_credit_code_with_colon_separator() -> None:
    value = "丹阳市双跃危险货物运输有限公司：统一社会信用代码：91321181730111164"

    assert normalize_value("new_vehicle.owner", value) == "丹阳市双跃危险货物运输有限公司"


def test_normalizes_transfer_party_names_without_fuzzy_matching() -> None:
    assert (
        normalize_value("transfer.buyer_name", "某某运输（集团） 有限公司")
        == "某某运输集团有限公司"
    )
    assert normalize_value("transfer.seller_name", "某某运输有限公司") != normalize_value(
        "transfer.seller_name",
        "某某运输有限责任公司",
    )


def test_normalize_invoice_amount_ignores_currency_grouping_and_decimal_zeros() -> None:
    assert normalize_value("invoice.amount", "￥ 152,000") == "152000"
    assert normalize_value("invoice.amount", "152000.00") == "152000"


def test_normalize_business_dates_accepts_page_and_chinese_formats() -> None:
    assert normalize_value("invoice.invoice_date", "2026-07-29") == "2026-07-29"
    assert normalize_value("invoice.invoice_date", "2026年07月29日") == "2026-07-29"


def test_compare_equivalent_amount_displays_image_value_like_page() -> None:
    """金额等价值显示页面写法。

    比对行为断言在 ``tests/test_aggregate.py``；本文件只保留归一化契约。
    """
    assert normalize_value("invoice.amount", "152000.00") == normalize_value(
        "invoice.amount", "￥ 152,000"
    )


def test_declared_normalizer_governs_over_the_field_name(monkeypatch) -> None:
    """字段声明里的归一化器优先；按字段名分派只是没声明时的兼容兜底。

    这条断言是"声明式配置真的生效"的证据：`old_vehicle.owner` 这个名字
    走不到金额归一化，只有声明 `normalizer="amount"` 才会。
    """
    from forj_review_service.businesses import field_policies

    class DeclaredPolicy:
        normalizer = "amount"

    monkeypatch.setattr(
        field_policies, "field_policy", lambda _field, *, business: DeclaredPolicy()
    )

    assert normalize_value("old_vehicle.owner", "￥1,000") == "1000"


def test_unknown_declared_normalizer_falls_back_to_the_field_name(monkeypatch) -> None:
    from forj_review_service.businesses import field_policies

    class UnknownPolicy:
        normalizer = "no_such_normalizer"

    monkeypatch.setattr(field_policies, "field_policy", lambda _field, *, business: UnknownPolicy())

    # 回落按字段名分派：.vin 仍然走 VIN 归一化。
    assert normalize_value("old_vehicle.vin", "ab c") == "ABC"

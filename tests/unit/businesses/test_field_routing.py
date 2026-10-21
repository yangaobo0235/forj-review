from forj_review_service.businesses.routing import (
    normalize_document_type,
    route_fields,
)
from tests.support.pages import SCRAP_BUSINESS as TEST_BUSINESS


def test_vehicle_fields_are_mapped_by_business_scope() -> None:
    fields = {"vehicle.vin": "VIN-1", "vehicle.plate_no": "PLATE-1", "vehicle.owner": "OWNER-1"}

    old_fields, old_limitation = route_fields(
        "old_vehicle", "vehicle_license", fields, pack=TEST_BUSINESS.pack
    )
    new_fields, new_limitation = route_fields(
        "new_vehicle", "vehicle_license", fields, pack=TEST_BUSINESS.pack
    )

    assert old_fields == {
        "old_vehicle.vin": "VIN-1",
        "old_vehicle.plate_no": "PLATE-1",
        "old_vehicle.owner": "OWNER-1",
    }
    assert new_fields == {
        "new_vehicle.vin": "VIN-1",
        "new_vehicle.plate_no": "PLATE-1",
        "new_vehicle.owner": "OWNER-1",
    }
    assert old_limitation is None
    assert new_limitation is None


def test_registration_brand_only_routes_to_the_active_jiefang_flag():
    routed, limitation = route_fields(
        "old_vehicle",
        "registration_certificate",
        {"vehicle.brand": "解放牌"},
        pack=TEST_BUSINESS.pack,
    )
    assert routed == {"old_vehicle.is_jiefang": "解放牌"}
    assert limitation is None


def test_legacy_vehicle_prefix_is_remapped_to_authoritative_scope() -> None:
    routed, limitation = route_fields(
        "new_vehicle",
        "old_vehicle",
        {"old_vehicle.vin": "NEW-VIN", "old_vehicle.plate_no": "NEW-PLATE"},
        pack=TEST_BUSINESS.pack,
    )

    assert normalize_document_type("old_vehicle") == "vehicle_license"
    assert routed == {
        "new_vehicle.vin": "NEW-VIN",
        "new_vehicle.plate_no": "NEW-PLATE",
    }
    assert limitation is None


def test_unknown_scope_does_not_route_vehicle_fields() -> None:
    routed, limitation = route_fields(
        "unknown", "vehicle_license", {"vehicle.vin": "VIN-1"}, pack=TEST_BUSINESS.pack
    )

    assert routed == {}
    assert limitation == "图片业务归属无法确定，请人工复核"


def test_document_type_must_be_compatible_with_business_scope() -> None:
    scrap_fields, scrap_limitation = route_fields(
        "new_vehicle", "scrap_certificate", {"old_vehicle.vin": "VIN-1"}, pack=TEST_BUSINESS.pack
    )
    invoice_fields, invoice_limitation = route_fields(
        "old_vehicle", "invoice", {"invoice.code": "CODE-1"}, pack=TEST_BUSINESS.pack
    )

    assert scrap_fields == {}
    assert "回收证明" in str(scrap_limitation)
    assert invoice_fields == {}
    assert "发票" in str(invoice_limitation)


def test_old_registration_certificate_does_not_route_owner() -> None:
    routed, limitation = route_fields(
        "old_vehicle",
        "registration_certificate",
        {
            "vehicle.owner": "OWNER",
            "vehicle.vin": "VIN",
            "vehicle.engine_model": "ENGINE",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert routed == {
        "old_vehicle.vin": "VIN",
        "old_vehicle.engine_model": "ENGINE",
    }
    assert limitation is None


def test_new_registration_certificate_routes_vin_but_not_owner() -> None:
    routed, limitation = route_fields(
        "new_vehicle",
        "registration_certificate",
        {
            "vehicle.owner": "OWNER",
            "vehicle.vin": "VIN",
            "vehicle.engine_model": "MUST-BE-DROPPED",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert routed == {
        "new_vehicle.vin": "VIN",
    }
    assert limitation is None


def test_registration_certificate_drops_owner_and_other_fields_outside_exact_allowlist() -> None:
    routed, limitation = route_fields(
        "new_vehicle",
        "registration_certificate",
        {
            "vehicle.owner": "OWNER",
            "vehicle.vin": "VIN",
            "vehicle.plate_no": "MUST-BE-DROPPED",
            "old_vehicle.owner": "MUST-BE-DROPPED",
            "new_vehicle.vin": "MUST-BE-DROPPED",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert routed == {
        "new_vehicle.vin": "VIN",
    }
    assert limitation is None


def test_routes_invoice_policy_fields_only_from_new_vehicle_scope() -> None:
    routed, limitation = route_fields(
        "new_vehicle",
        "invoice",
        {
            "invoice.invoice_no": "INV-001",
            "invoice.invoice_date": "2026-09-10",
            "new_vehicle.origin": "长春市",
            "invoice.phone": "0431-12345678",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert routed == {
        "invoice.code": "INV-001",
        "invoice.invoice_no": "INV-001",
        "invoice.invoice_date": "2026-09-10",
        "new_vehicle.origin": "长春市",
    }
    assert limitation is None


def test_invoice_buyer_name_supports_customer_name_and_new_vehicle_owner() -> None:
    routed, limitation = route_fields(
        "new_vehicle", "invoice", {"vehicle.owner": "甲运输有限公司"}, pack=TEST_BUSINESS.pack
    )

    assert routed == {
        "new_vehicle.owner": "甲运输有限公司",
        "application.customer_name": "甲运输有限公司",
    }
    assert limitation is None


def test_routes_business_license_fields_without_vehicle_scope() -> None:
    routed, limitation = route_fields(
        "business_license",
        "business_license",
        {
            "business_license.company_name": "甲运输有限公司",
            "business_license.legal_representative": "张三",
            "business_license.unified_social_credit_code": "91230000ABC",
            "vehicle.owner": "DROP",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert routed == {
        "business_license.company_name": "甲运输有限公司",
        "business_license.legal_representative": "张三",
        "business_license.unified_social_credit_code": "91230000ABC",
    }
    assert limitation is None


def test_routes_identity_card_name_and_side_without_sensitive_fields() -> None:
    routed, limitation = route_fields(
        "identity",
        "id_card",
        {
            "identity_card.name": "张三",
            "identity_card.side": "FRONT",
            "identity_card.number": "MUST-BE-DROPPED",
            "identity_card.address": "MUST-BE-DROPPED",
        },
        pack=TEST_BUSINESS.pack,
    )

    assert normalize_document_type("id_card") == "identity_card"
    assert routed == {
        "identity_card.name": "张三",
        "identity_card.side": "FRONT",
    }
    assert limitation is None

from dataclasses import replace

from forj_review_service.businesses.packs.model import FieldDeclaration, QrFieldBinding
from forj_review_service.contracts.review import FieldObservation, QrCheck
from forj_review_service.evidence.qr import append_qr_observations
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO


def test_qr_evidence_uses_declared_fields_and_ignores_unknown_website_properties():
    pack = replace(
        SCRAP_REPLACEMENT_QINGDAO.pack_declaration,
        fields=(FieldDeclaration("permit.number", "许可证号"),),
        qr_field_bindings=(QrFieldBinding("number", "permit.number"),),
    )
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, pack_declaration=pack)
    checks = [
        QrCheck(
            image_index=0,
            status="MATCH",
            message="fixture",
            page_fields={
                "number": "AB123",
                "unexpected": "must-not-be-a-certificate-number",
            },
        )
    ]
    observations = [
        FieldObservation(
            field="permit.number",
            value="AB-123",
            source_type="image",
            source_id="fixture",
            image_index=0,
        )
    ]
    append_qr_observations(observations, checks, profile)
    assert [o.field for o in observations] == ["permit.number", "permit.number"]
    assert observations[-1].value == "AB123"
    assert observations[-1].source_type == "qr_page"
    assert checks[0].status == "MATCH"


def test_page_without_qr_bindings_cannot_receive_other_business_qr_fields():
    checks = [QrCheck(image_index=0, status="MATCH", message="fixture", page_fields={"vin": "VIN"})]
    observations = []
    append_qr_observations(
        observations,
        checks,
        replace(
            SCRAP_REPLACEMENT_QINGDAO,
            pack_declaration=replace(
                SCRAP_REPLACEMENT_QINGDAO.pack_declaration, qr_field_bindings=()
            ),
        ),
    )
    assert not observations


def test_failed_web_access_cannot_supply_official_field_evidence():
    pack = replace(
        SCRAP_REPLACEMENT_QINGDAO.pack_declaration,
        qr_field_bindings=(QrFieldBinding("number", "permit.number"),),
    )
    profile = replace(SCRAP_REPLACEMENT_QINGDAO, pack_declaration=pack)
    check = QrCheck(
        image_index=0, status="MATCH", message="fixture", page_fields={"number": "AB123"}
    )
    observations = [
        FieldObservation(
            field="permit.number",
            value="AB999",
            source_type="image",
            source_id="fixture",
            image_index=0,
            uncertain=True,
        )
    ]
    check.status = "REVIEW_REQUIRED"
    check.accessible = False
    append_qr_observations(observations, [check], profile)
    assert len(observations) == 1

from dataclasses import replace

from forj_review_assistant.businesses.packs.model import FieldDeclaration, QrFieldBinding
from forj_review_assistant.businesses.profiles import VEHICLE_SOURCE_DEFAULT
from forj_review_assistant.contracts.review import FieldObservation, QrCheck
from forj_review_assistant.evidence.qr import append_qr_observations, mark_qr_conflicts


def test_qr_evidence_uses_declared_fields_and_ignores_unknown_website_properties():
    pack = replace(
        VEHICLE_SOURCE_DEFAULT.pack_declaration,
        fields=(FieldDeclaration("permit.number", "许可证号"),),
        qr_field_bindings=(QrFieldBinding("number", "permit.number", "alphanumeric"),),
    )
    profile = replace(VEHICLE_SOURCE_DEFAULT, pack_declaration=pack)
    checks = [QrCheck(image_index=0, status="MATCH", message="fixture", page_fields={
        "number": "AB123", "unexpected": "must-not-be-a-certificate-number",
    })]
    observations = [FieldObservation(
        field="permit.number", value="AB-123", source_type="image", source_id="fixture", image_index=0,
    )]
    append_qr_observations(observations, checks, profile)
    assert [o.field for o in observations] == ["permit.number", "permit.number"]
    assert not mark_qr_conflicts(observations, checks, profile)
    observations[0].value = "AB-999"
    assert mark_qr_conflicts(observations, checks, profile)
    assert checks[0].status == "CONFLICT"


def test_page_without_qr_bindings_cannot_receive_other_business_qr_fields():
    checks = [QrCheck(image_index=0, status="MATCH", message="fixture", page_fields={"vin": "VIN"})]
    observations = []
    append_qr_observations(observations, checks, VEHICLE_SOURCE_DEFAULT)
    assert not observations

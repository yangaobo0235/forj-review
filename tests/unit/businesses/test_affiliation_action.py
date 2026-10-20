from types import SimpleNamespace

from forj_review_service.businesses.rules.affiliation_action import run_set_affiliation
from forj_review_service.contracts.review import BusinessType, FieldObservation, ImageInput


def context(*document_types: str, image_hints: tuple[str, ...] = ()):
    return SimpleNamespace(
        profile=SimpleNamespace(business_type=BusinessType.SCRAP_REPLACEMENT),
        observations=tuple(
            FieldObservation(
                field="business_license.company_name"
                if document_type == "business_license"
                else "identity_card.name",
                source_type="image",
                source_id=f"{document_type}-{index}",
                document_type=document_type,
                value="甲有限公司" if document_type == "business_license" else "张三",
            )
            for index, document_type in enumerate(document_types)
        ),
        request=SimpleNamespace(
            images=[
                ImageInput(index=index, src=f"https://example.test/{index}.jpg", category_hint=hint)
                for index, hint in enumerate(image_hints)
            ]
        ),
        batch=SimpleNamespace(recognized_documents=[]),
    )


def test_business_license_proposes_company_affiliation_action() -> None:
    result = run_set_affiliation(
        context("business_license")
    )

    assert len(result.page_action_intents) == 1
    intent = result.page_action_intents[0]
    assert intent.action_id == "set_affiliation"
    assert intent.payload == {
        "fields": ["old_vehicle.affiliation", "new_vehicle.affiliation"],
        "value": "公司",
    }
    assert intent.requires_authorization is False


def test_identity_card_proposes_personal_affiliation_action() -> None:
    result = run_set_affiliation(context("identity_card"))

    assert result.page_action_intents[0].payload["value"] == "个人"


def test_both_affiliation_materials_do_not_propose_an_action() -> None:
    result = run_set_affiliation(context("business_license", "identity_card"))

    assert result.page_action_intents == ()


def test_missing_affiliation_material_does_not_propose_an_action() -> None:
    result = run_set_affiliation(context())

    assert result.page_action_intents == ()


def test_uploaded_material_hint_can_drive_the_action_when_ocr_has_no_fields() -> None:
    result = run_set_affiliation(context(image_hints=("business_license",)))

    assert result.page_action_intents[0].payload["value"] == "公司"


def test_both_uploaded_material_hints_suppress_the_action() -> None:
    result = run_set_affiliation(
        context(image_hints=("business_license", "id_card"))
    )

    assert result.page_action_intents == ()

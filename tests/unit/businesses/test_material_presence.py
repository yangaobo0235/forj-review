"""上传完整性样本均为合成元数据，不使用真实材料。"""

import pytest

from forj_review_service.businesses.completeness import evaluate_extracted, evaluate_presence
from forj_review_service.contracts.recognition import DocumentClassification, RecognitionBatchResult
from forj_review_service.contracts.review import ReviewRequest
from tests.support.pages import (
    SCRAP_REPLACEMENT_CHANGCHUN,
    SCRAP_REPLACEMENT_QINGDAO,
    TRANSFER_DEFAULT,
    VEHICLE_SOURCE_DEFAULT,
)


def material(index, kind, scope, sides=(), *, labelled=True):
    return {
        "index": index,
        "imageId": f"synthetic-{index}",
        "src": f"https://example.test/{index}.jpg",
        "businessScope": scope,
        "documentTypeHint": kind if labelled else "unknown",
        "identitySidesHint": list(sides),
    }


def vehicles():
    # 有意打乱顺序，第一张旧车资料是报废证明。
    return [
        material(i, kind, scope)
        for i, (kind, scope) in enumerate(
            [
                ("scrap_certificate", "old_vehicle"),
                ("vehicle_license", "new_vehicle"),
                ("registration_certificate", "old_vehicle"),
                ("invoice", "new_vehicle"),
                ("vehicle_license", "old_vehicle"),
                ("registration_certificate", "new_vehicle"),
            ]
        )
    ]


def confirmed(images):
    # Synthetic content-recognition results; labels alone are tested separately.
    return RecognitionBatchResult(
        document_classifications=[
            DocumentClassification(
                target_id=i["imageId"],
                document_type=i["documentTypeHint"],
                business_scope=i["businessScope"],
                identity_sides=i["identitySidesHint"],
            )
            for i in images
            if i["documentTypeHint"] != "unknown"
        ]
    )


def report(images, batch=None, **diagnostics):
    request = ReviewRequest(
        page_url="https://example.test", images=images, collection_diagnostics=diagnostics
    )
    return evaluate_presence(
        request,
        SCRAP_REPLACEMENT_QINGDAO.material_policy,
        batch if batch is not None else confirmed(images),
    )


@pytest.mark.parametrize("profile", [SCRAP_REPLACEMENT_QINGDAO, SCRAP_REPLACEMENT_CHANGCHUN])
@pytest.mark.parametrize(
    "subject",
    [
        [material(6, "business_license", "business_license")],
        [
            material(6, "identity_card", "identity", ["FRONT"]),
            material(7, "identity_card", "identity", ["BACK"]),
        ],
        [
            material(6, "business_license", "business_license"),
            material(7, "identity_card", "identity", ["FRONT"]),
            material(8, "identity_card", "identity", ["BACK"]),
        ],
        [material(6, "identity_card", "identity", ["FRONT", "BACK"])],
    ],
)
def test_complete_uploads_accept_either_subject_option_in_both_cities(profile, subject):
    request = ReviewRequest(page_url="https://example.test", images=[*vehicles(), *subject])
    result = evaluate_extracted(request, profile, confirmed([*vehicles(), *subject]))
    assert result.presence.status == "COMPLETE"
    assert all(group.status == "PRESENT" for group in result.presence.groups)
    # 已确认材料类型；字段、登记证页码未识别，不伪造质量通过。
    assert result.status != "COMPLETE"
    assert all(
        not item.required_pages
        for group in result.presence.groups
        for option in group.alternatives
        for item in option
    )


@pytest.mark.parametrize("sides", [(), ("FRONT",), ("BACK",)])
def test_identity_pair_requires_both_sides(sides):
    result = report([*vehicles(), material(6, "identity_card", "identity", sides)])
    subject = result.groups[-1]
    assert subject.status == ("UNCERTAIN" if not sides else "MISSING")
    assert result.status != "COMPLETE"


def test_repeated_front_images_do_not_supply_back_or_business_license():
    result = report(
        [
            *vehicles(),
            material(6, "identity_card", "identity", ["FRONT"]),
            material(7, "identity_card", "identity", ["FRONT"]),
        ]
    )
    assert result.status == "INCOMPLETE"
    assert [item.status for item in result.groups[-1].alternatives[1]] == ["PRESENT", "MISSING"]


def test_business_license_satisfies_subject_even_with_an_incomplete_identity_pair():
    result = report(
        [
            *vehicles(),
            material(6, "business_license", "business_license"),
            material(7, "identity_card", "identity", ["FRONT"]),
        ]
    )
    assert result.status == "COMPLETE"
    assert result.groups[-1].alternatives[1][1].status == "MISSING"


def test_same_count_with_duplicate_kind_is_incomplete_and_scopes_never_cross():
    images = vehicles()
    images[0]["documentTypeHint"] = "vehicle_license"
    images[5]["businessScope"] = "old_vehicle"
    result = report([*images, material(6, "business_license", "business_license")])
    assert result.status == "INCOMPLETE"
    assert result.groups[0].alternatives[0][2].status == "MISSING"
    assert result.groups[1].alternatives[0][1].status == "MISSING"


@pytest.mark.parametrize("scope", ["old_vehicle", "unknown"])
def test_unclassified_images_make_possible_missing_kinds_uncertain(scope):
    result = report([material(0, "unknown", scope, labelled=False)])
    assert all(item.status == "UNCERTAIN" for item in result.groups[0].alternatives[0])
    assert result.groups[1].status == ("UNCERTAIN" if scope == "unknown" else "MISSING")


def test_reliable_content_classification_survives_failed_field_extraction():
    images = [material(0, "unknown", "old_vehicle", labelled=False)]
    batch = RecognitionBatchResult(
        failed_count=1,
        failed_image_ids=["synthetic-0"],
        document_classifications=[
            DocumentClassification(
                target_id="synthetic-0",
                document_type="scrap_certificate",
                business_scope="old_vehicle",
            ),
        ],
    )
    result = report(images, batch)
    old = result.groups[0].alternatives[0]
    assert [item.status for item in old] == ["MISSING", "MISSING", "PRESENT"]
    assert old[2].image_ids == ["synthetic-0"]
    quality = evaluate_extracted(
        ReviewRequest(page_url="https://example.test", images=images),
        SCRAP_REPLACEMENT_QINGDAO,
        batch,
    )
    assert quality.checklist[2].status == "UNCERTAIN"
    assert any(issue.reason_code == "recognition_failed" for issue in quality.issues)


def test_classified_identity_has_independent_side_presence():
    images = [material(0, "unknown", "identity", labelled=False)]
    batch = RecognitionBatchResult(
        document_classifications=[
            DocumentClassification(
                target_id="synthetic-0",
                document_type="identity_card",
                business_scope="identity",
                identity_sides=["BACK"],
            )
        ]
    )
    assert [item.status for item in report(images, batch).groups[-1].alternatives[1]] == [
        "MISSING",
        "PRESENT",
    ]


def test_collection_failure_does_not_erase_confirmed_content_type():
    images = vehicles()
    images[0]["collectionError"] = "synthetic read error"
    assert report(images).groups[0].status == "PRESENT"


@pytest.mark.parametrize(
    "diagnostics", [{"image_overflow": True}, {"collection_issues": ["synthetic failure"]}]
)
def test_collection_uncertainty_remains_visible(diagnostics):
    result = report([], **diagnostics)
    assert result.status == "UNCERTAIN"
    assert result.notices
    assert all(group.status == "UNCERTAIN" for group in result.groups)


def test_extra_image_overflow_does_not_erase_fully_confirmed_required_uploads():
    result = report(
        [*vehicles(), material(6, "business_license", "business_license")], image_overflow=True
    )
    assert result.status == "COMPLETE"
    assert result.notices


def test_unconfigured_presence_keeps_existing_business_contract():
    assert evaluate_presence(ReviewRequest(page_url="https://example.test"), None) is None


@pytest.mark.parametrize(
    "profile",
    [
        SCRAP_REPLACEMENT_QINGDAO,
        SCRAP_REPLACEMENT_CHANGCHUN,
        TRANSFER_DEFAULT,
        VEHICLE_SOURCE_DEFAULT,
    ],
)
def test_all_pages_wait_for_content_classification_and_update_progressively(profile):
    requirements = [
        option
        for group in profile.material_policy.presence_groups
        for option in group.alternatives[:1]
    ]
    images = [
        material(
            i,
            item.document_type,
            item.business_scope,
            [item.identity_side] if item.identity_side else [],
        )
        for i, item in enumerate(item for option in requirements for item in option)
    ]
    request = ReviewRequest(page_url="https://example.test", images=images)
    assert evaluate_presence(request, profile.material_policy).status != "COMPLETE"
    batch = confirmed(images)
    assert evaluate_presence(request, profile.material_policy, batch).status == "COMPLETE"
    batch.document_classifications.pop()
    partial = evaluate_presence(request, profile.material_policy, batch)
    assert partial.status != "COMPLETE"
    assert (
        any(
            item.status == "PRESENT"
            for group in partial.groups
            for option in group.alternatives
            for item in option
        )
        or len(images) == 1
    )


def test_wrong_upload_label_cannot_confirm_a_material():
    images = [material(0, "scrap_certificate", "old_vehicle")]
    batch = confirmed([material(0, "vehicle_license", "old_vehicle")])
    result = report(images, batch)
    assert result.groups[0].alternatives[0][2].status == "MISSING"
    assert result.groups[0].alternatives[0][0].status == "PRESENT"


def test_classified_scope_never_confirms_the_other_vehicle_group():
    images = [material(0, "vehicle_license", "unknown")]
    batch = confirmed([material(0, "vehicle_license", "new_vehicle")])
    result = report(images, batch)
    assert result.groups[0].alternatives[0][0].status == "MISSING"
    assert result.groups[1].alternatives[0][0].status == "PRESENT"

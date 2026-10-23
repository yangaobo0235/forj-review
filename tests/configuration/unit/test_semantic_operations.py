"""Representative high-frequency operations remain configuration tasks, including combinations."""

from copy import deepcopy

import pytest

from forj_review_service.businesses.page_config.maintenance import (
    edit_fields,
    validate_configurable_change,
)
from forj_review_service.businesses.page_config.row_contract import split_definition
from tests.support.pages import SCRAP_REPLACEMENT_QINGDAO, VEHICLE_SOURCE_DEFAULT, page_definition


@pytest.mark.parametrize(
    "key,value",
    [
        ("invoice_date_from", "2026-02-01"),
        ("invoice_date_to", "2026-11-30"),
        ("disposal_date_from", "2026-02-01"),
        ("disposal_deadline", "2026-11-30"),
    ],
)
def test_policy_dates_use_real_typed_fields_and_are_consumed(key, value):
    page = page_definition(SCRAP_REPLACEMENT_QINGDAO)
    candidate = edit_fields(page, [{"op": "set_policy_dates", "value": {key: value}}])
    assert candidate["settings"]["replacement_policy"][key] == value
    assert candidate["page_id"] == page["page_id"]


def test_combination_cannot_widen_developer_attributes():
    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    for key in ("uncertain_requires_review", "material_field", "task_projector"):
        candidate = deepcopy(page)
        field = next(f for f in candidate["fields"] if f["key"] == "vehicle.engine_no")
        field[key] = False if key == "uncertain_requires_review" else "invented"
        with pytest.raises(ValueError):
            validate_configurable_change(page, candidate)


def test_field_label_material_policy_and_dates_form_one_candidate():
    page = page_definition(SCRAP_REPLACEMENT_QINGDAO)
    policy = split_definition(page)["material_policy"]
    candidate = edit_fields(
        page,
        [
            {
                "op": "upsert_field",
                "value": {"key": "invoice.invoice_date", "label": "新车开票日期"},
            },
            {"op": "set_material_policy", "value": policy},
            {"op": "set_policy_dates", "value": {"invoice_date_to": "2026-11-30"}},
            {"op": "set_presentation", "value": split_definition(page)["presentation"]},
        ],
    )
    assert (
        next(f for f in candidate["fields"] if f["key"] == "invoice.invoice_date")["label"]
        == "新车开票日期"
    )
    assert candidate["settings"]["replacement_policy"]["invoice_date_to"] == "2026-11-30"
    assert page["settings"]["replacement_policy"]["invoice_date_to"] == "2026-12-31"


def test_semantic_policy_command_rejects_arbitrary_dates_and_business_changes():
    page = page_definition(SCRAP_REPLACEMENT_QINGDAO)
    for value in ({"invented_date": "2026-01-01"}, {"region": "other"}, {}):
        with pytest.raises(ValueError):
            edit_fields(page, [{"op": "set_policy_dates", "value": value}])


def test_compact_capabilities_keep_executable_field_label_guidance():
    import json

    from forj_review_config_service.contracts.tool_results import result_reference
    from forj_review_service.businesses.page_config.maintenance import maintenance_contract

    page = page_definition(VEHICLE_SOURCE_DEFAULT)
    # Full schema content is paged. The first model-visible summary must still
    # distinguish field labels from page-level presentation options.
    visible = json.loads(
        result_reference(
            "capabilities",
            json.dumps(
                {"maintenance": maintenance_contract(page), "schema": {"description": "x" * 10000}}
            ),
        )
    )
    guide = visible["summary"]["maintenance"]
    assert "label" in guide["field_properties"]
    assert "upsert_field" in guide["operation_inputs"]
    operation = {"op": "upsert_field", "value": {"key": "vehicle.engine_no", "label": "发动机号码"}}
    candidate = edit_fields(page, [operation])
    assert (
        next(f for f in candidate["fields"] if f["key"] == "vehicle.engine_no")["label"]
        == "发动机号码"
    )
    assert candidate["settings"]["presentation"] == page["settings"]["presentation"]

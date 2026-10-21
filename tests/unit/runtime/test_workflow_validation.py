"""Only explicit input failures become manual-review responses."""

from types import SimpleNamespace

import pytest

from forj_review_service.contracts.errors import ReviewInputError
from forj_review_service.runtime.workflow.graph import ReviewWorkflow


def test_internal_validation_failure_propagates_instead_of_becoming_a_review_result():
    def broken(state):
        raise ValueError("private implementation detail")

    with pytest.raises(ValueError, match="private implementation detail"):
        ReviewWorkflow._validate_input(SimpleNamespace(_validate_context=broken), {})


def test_explicit_input_failure_retains_its_public_explanation():
    def invalid(state):
        raise ReviewInputError("请求与审核配置不一致")

    assert ReviewWorkflow._validate_input(SimpleNamespace(_validate_context=invalid), {}) == {
        "validation_error": "请求与审核配置不一致"
    }

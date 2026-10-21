import pytest

from forj_review_service.capabilities.business_rules import (
    BusinessRuleRegistry,
    UnknownBusinessRule,
)
from forj_review_service.capabilities.specs import (
    ReviewExecutionContext,
    RuleExecutionResult,
)
from forj_review_service.contracts.recognition import CheckResult, RecognitionBatchResult
from forj_review_service.contracts.review import ReviewRequest
from tests.support.pages import TRANSFER_DEFAULT


def context():
    return ReviewExecutionContext(
        request=ReviewRequest(
            page_url="https://example.test/transfer",
            business_type="transfer",
            region="default",
        ),
        profile=TRANSFER_DEFAULT,
        batch=RecognitionBatchResult(),
        observations=(),
    )


@pytest.mark.asyncio
async def test_registry_runs_only_configured_rules_in_order() -> None:
    registry = BusinessRuleRegistry(
        {
            "first": lambda _: RuleExecutionResult(
                checks=(
                    CheckResult(check_id="FIRST", label="第一项", status="MATCH", reason="通过"),
                )
            ),
            "second": lambda _: RuleExecutionResult(
                checks=(
                    CheckResult(check_id="SECOND", label="第二项", status="MATCH", reason="通过"),
                )
            ),
        }
    )

    result = await registry.execute(("second", "first"), context())

    assert [item.check_id for item in result.checks] == ["SECOND", "FIRST"]


@pytest.mark.asyncio
async def test_registry_rejects_unknown_rule() -> None:
    with pytest.raises(UnknownBusinessRule, match="missing"):
        await BusinessRuleRegistry({}).execute(("missing",), context())


@pytest.mark.asyncio
async def test_registry_awaits_async_rules_and_deduplicates_in_requested_order():
    calls = []

    async def asynchronous(_):
        calls.append("async")
        return RuleExecutionResult(
            checks=(CheckResult(check_id="ASYNC", label="异步规则", status="MATCH", reason="通过"),)
        )

    def synchronous(_):
        calls.append("sync")
        return RuleExecutionResult()

    result = await BusinessRuleRegistry({"async": asynchronous, "sync": synchronous}).execute(
        ("async", "sync", "async"), context()
    )
    assert calls == ["async", "sync"]
    assert [item.check_id for item in result.checks] == ["ASYNC"]

"""能力执行出口的结构化事件。

每一项能力的终态都必须留下一条 `review capability completed` 事件：没有它，
排查一次降级只能去翻响应里的 `capability_results`，看不到控制台。
"""

import logging

import pytest

from forj_review_service.businesses.profiles import TRANSFER_DEFAULT
from forj_review_service.capabilities.registry import CapabilityRegistry
from forj_review_service.capabilities.specs import (
    CapabilityResult,
    CapabilitySpec,
    ReviewExecutionContext,
)
from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import ReviewRequest
from forj_review_service.runtime.workflow.planner import CapabilityPlanItem


def context(trace_id: str = "trace-1") -> ReviewExecutionContext:
    return ReviewExecutionContext(
        request=ReviewRequest(
            page_url="https://example.test/review",
            business_type="transfer",
            region="default",
            trace_id=trace_id,
        ),
        profile=TRANSFER_DEFAULT,
        batch=RecognitionBatchResult(),
        observations=(),
    )


def registry(spec: CapabilitySpec, handler) -> CapabilityRegistry:
    return CapabilityRegistry({spec.capability_id: handler}, {spec.capability_id: spec})


@pytest.mark.asyncio
async def test_successful_capability_logs_its_outcome_with_the_trace_id(caplog) -> None:
    spec = CapabilitySpec("demo")
    calls: list[str] = []

    async def handler(_context, inner_spec):
        calls.append(inner_spec.capability_id)
        return CapabilityResult(capability_id="demo", status="SUCCEEDED")

    with caplog.at_level(logging.INFO, logger="forj_review_service.capabilities.registry"):
        result = await registry(spec, handler).execute(
            spec, CapabilityPlanItem("demo", "READY"), context("abc123")
        )

    assert result.status == "SUCCEEDED"
    assert calls == ["demo"]
    assert caplog.text.count("review capability completed") == 1
    assert "capability=demo" in caplog.text
    assert "status=SUCCEEDED" in caplog.text
    assert "trace_id=abc123" in caplog.text


@pytest.mark.asyncio
async def test_capability_that_never_ran_is_still_logged(caplog) -> None:
    """计划阶段就判定不可用的能力不执行处理器，但同样要留下事件。"""
    spec = CapabilitySpec("demo")

    async def handler(_context, _spec):
        raise AssertionError("未 READY 的能力不应执行处理器")

    with caplog.at_level(logging.INFO, logger="forj_review_service.capabilities.registry"):
        result = await registry(spec, handler).execute(
            spec, CapabilityPlanItem("demo", "SKIPPED", "材料缺失"), context()
        )

    assert result.status == "SKIPPED"
    assert caplog.text.count("review capability completed") == 1
    assert "status=SKIPPED" in caplog.text


@pytest.mark.asyncio
async def test_failed_capability_logs_exactly_one_outcome(caplog) -> None:
    spec = CapabilitySpec("demo", failure_policy="MANUAL_REVIEW")

    async def handler(_context, _spec):
        raise RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger="forj_review_service.capabilities.registry"):
        result = await registry(spec, handler).execute(
            spec, CapabilityPlanItem("demo", "READY"), context()
        )

    assert result.status == "FAILED"
    assert caplog.text.count("review capability completed") == 1
    assert "status=FAILED" in caplog.text


@pytest.mark.asyncio
async def test_retried_capability_logs_one_outcome_with_the_attempt_count(caplog) -> None:
    """重试成功只留一条事件，但 attempts 必须反映真实轮次。"""
    spec = CapabilitySpec("demo", retries=1)
    attempts: list[int] = []

    async def handler(_context, _spec):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("第一次失败")
        return CapabilityResult(capability_id="demo", status="SUCCEEDED")

    with caplog.at_level(logging.INFO, logger="forj_review_service.capabilities.registry"):
        result = await registry(spec, handler).execute(
            spec, CapabilityPlanItem("demo", "READY"), context()
        )

    assert result.status == "SUCCEEDED"
    assert attempts == [1, 2]
    assert caplog.text.count("review capability completed") == 1
    assert "attempts=2" in caplog.text

import asyncio
import threading

import pytest

from forj_review_assistant.contracts.recognition import AgentBatchResult
from forj_review_assistant.contracts.review import (
    JobStatus,
    ReviewJobPhase,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_assistant.runtime.jobs.manager import ReviewJobManager
from forj_review_assistant.runtime.scheduling.fair_scheduler import FairAsyncScheduler


class UrlService:
    def __init__(self):
        self.extracted_ids = []
        self.final_batches = []

    async def extract_image_async(self, request, image):
        assert image.recognition_source == image.src
        image_id = str(image.image_id or image.index)
        self.extracted_ids.append(image_id)
        return AgentBatchResult(total_count=1, completed_count=1, completed_image_ids=[image_id])

    async def assist_with_batch_async(self, request, initial_batch):
        self.final_batches.append(initial_batch.model_copy(deep=True))
        return ReviewResponse(recommendation="REVIEW_REQUIRED", risk_level="MEDIUM", summary="全部完成"), initial_batch


def request(count=2):
    return ReviewRequest(page_url="https://example.test/review", images=[
        {"index": i, "image_id": f"image-{i}", "src": f"https://images.example.test/{i}.jpg",
         "business_scope": "old_vehicle" if i == 0 else "new_vehicle"} for i in range(count)
    ])


def test_url_job_schedules_each_image_once_and_reuses_batch():
    service = UrlService()
    manager = ReviewJobManager(service)
    created = manager.create_urls(request(3))
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.COMPLETED
    assert snapshot.phase is ReviewJobPhase.COMPLETED
    assert snapshot.progress.submitted_count == snapshot.progress.completed_count == 3
    assert sorted(service.extracted_ids) == ["image-0", "image-1", "image-2"]
    assert len(service.final_batches) == 1
    assert service.final_batches[0].completed_count == 3
    assert snapshot.groups["old_vehicle"].completed_count == 1
    assert snapshot.groups["new_vehicle"].completed_count == 2
    assert snapshot.result.summary == "全部完成"


@pytest.mark.parametrize("src", ["", "blob:https://example.test/id", "data:image/jpeg;base64,AA==", "file:///tmp/image.png"])
def test_invalid_urls_are_rejected_before_job_creation(src):
    manager = ReviewJobManager(UrlService())
    data = request(1)
    data.images[0].src = src
    with pytest.raises(ValueError, match="HTTP"):
        manager.create_urls(data)
    assert not manager._jobs


def test_collection_errors_are_archived_without_model_call():
    service = UrlService()
    manager = ReviewJobManager(service)
    data = request(1)
    data.images[0].collection_error = "模型无法访问图片地址"
    created = manager.create(data)
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.PARTIAL
    assert snapshot.progress.failed_count == 1
    assert snapshot.failed_image_ids == ["image-0"]
    assert service.extracted_ids == []


def test_missing_terminal_results_are_partial():
    class Incomplete(UrlService):
        async def extract_image_async(self, request, image):
            return AgentBatchResult(total_count=1)
    manager = ReviewJobManager(Incomplete())
    created = manager.create(request(1))
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.PARTIAL
    assert all(group.status is not JobStatus.RUNNING for group in snapshot.groups.values())


def test_timeouts_are_partial_and_retained():
    class Timeout(UrlService):
        async def extract_image_async(self, request, image):
            return AgentBatchResult(total_count=1, timed_out_count=1, timed_out_image_ids=[str(image.image_id)])
    manager = ReviewJobManager(Timeout())
    created = manager.create(request(1))
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.PARTIAL
    assert snapshot.progress.timed_out_count == 1
    assert snapshot.timed_out_image_ids == ["image-0"]
    assert snapshot.groups["old_vehicle"].timed_out_count == 1


def test_model_exception_does_not_lose_other_results():
    class Failing(UrlService):
        async def extract_image_async(self, request, image):
            if image.index == 0:
                raise RuntimeError("synthetic failure")
            return await super().extract_image_async(request, image)
    manager = ReviewJobManager(Failing())
    created = manager.create(request())
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.PARTIAL
    assert snapshot.progress.failed_count == snapshot.progress.completed_count == 1
    assert snapshot.failed_image_ids == ["image-0"]
    assert snapshot.timed_out_image_ids == []


def test_finalization_failure_only_exposes_exception_type(caplog):
    class Failing(UrlService):
        async def assist_with_batch_async(self, request, initial_batch):
            raise AttributeError("private model details")
    manager = ReviewJobManager(Failing())
    with caplog.at_level("ERROR", logger="forj_review_assistant"):
        created = manager.create(request(0))
        assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.status is JobStatus.FAILED
    assert snapshot.phase is ReviewJobPhase.FAILED
    assert snapshot.message == "审核任务失败：AttributeError"
    assert any(record.exc_info and record.exc_info[0] is AttributeError for record in caplog.records)


def test_groups_use_scope_when_category_hint_disagrees():
    manager = ReviewJobManager(UrlService())
    data = request(1)
    data.images[0].business_scope = "new_vehicle"
    data.images[0].category_hint = "old_vehicle"
    created = manager.create(data)
    assert manager.wait(created.job_id, 2)
    snapshot = manager.get(created.job_id)
    assert snapshot.groups["old_vehicle"].total_count == 0
    assert snapshot.groups["new_vehicle"].completed_count == 1


def test_custom_page_progress_uses_declared_scopes():
    from forj_review_assistant.businesses.configuration.compiler import compile_page
    from forj_review_assistant.businesses.configuration.schema import PageDefinition
    from forj_review_assistant.businesses.registry import BusinessRegistry

    profile = compile_page(PageDefinition.model_validate({
        "page_id": "custom", "business_type": "custom", "title": "自定义页面",
        "paths": ["/custom"], "scopes": ["permit", "attachment"],
        "fields": [{"key": "custom.name", "label": "名称", "aliases": ["名称"], "mode": "SYSTEM"}],
    }))
    service = UrlService()
    service.registry = BusinessRegistry((profile,))
    manager = ReviewJobManager(service)
    try:
        data = ReviewRequest(page_id="custom", business_type="custom", page_url="https://example.test/custom", images=[{
            "index": 0, "src": "https://example.test/synthetic.jpg", "business_scope": "permit",
        }])
        created = manager.create(data)
        assert manager.wait(created.job_id, 2)
        groups = manager.get(created.job_id).groups
        assert set(groups) == {"permit", "attachment"}
        assert groups["permit"].completed_count == 1
        assert groups["attachment"].total_count == 0
    finally:
        manager.close()


def test_duplicate_ids_and_excessive_counts_are_rejected():
    manager = ReviewJobManager(UrlService())
    data = request()
    data.images[1].image_id = data.images[0].image_id
    with pytest.raises(ValueError, match="重复标识"):
        manager.create(data)
    with pytest.raises(ValueError, match="最多"):
        manager.create(request(17))
    data = request()
    data.images[1].index = data.images[0].index
    with pytest.raises(ValueError, match="重复序号"):
        manager.create(data)


def test_cancel_of_a_completed_job_preserves_the_final_snapshot():
    manager = ReviewJobManager(UrlService())
    created = manager.create(request(1))
    assert manager.wait(created.job_id, 2)
    snapshot = manager.cancel(created.job_id)
    assert snapshot.status is JobStatus.COMPLETED
    assert snapshot.result is not None


def test_cancel_removes_queued_images_and_never_finalizes():
    started, release = threading.Event(), threading.Event()
    class Blocking(UrlService):
        async def extract_image_async(self, request, image):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return await super().extract_image_async(request, image)
    service = Blocking()
    manager = ReviewJobManager(service, scheduler=FairAsyncScheduler(global_limit=1, per_job_limit=1))
    created = manager.create(request(3))
    assert started.wait(2)
    snapshot = manager.cancel(created.job_id)
    release.set()
    assert snapshot.status is JobStatus.CANCELLED
    assert snapshot.phase is ReviewJobPhase.CANCELLED
    assert manager.wait(created.job_id, 1)
    assert not service.final_batches


def test_phase_timing_snapshot_isolation_and_expiry(caplog):
    manager = ReviewJobManager(UrlService(), ttl_seconds=60)
    with caplog.at_level("INFO", logger="forj_review_assistant"):
        created = manager.create(request(1))
        assert manager.wait(created.job_id, 2)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("review job timing")]
    assert len(lines) == 1
    assert f"job_id={created.job_id}" in lines[0]
    for part in lines[0].split():
        key, _, value = part.partition("=")
        if key.endswith("_ms"):
            assert int(value) >= 0
    snapshot = manager.get(created.job_id)
    snapshot.progress.completed_count = 999
    assert manager.get(created.job_id).progress.completed_count == 1
    manager._jobs[created.job_id].updated_at -= 61
    with pytest.raises(KeyError):
        manager.get(created.job_id)


@pytest.mark.parametrize("outcome", ["FAILED", "CANCELLED"])
def test_every_terminal_outcome_logs_timing_once(outcome, caplog):
    entered = threading.Event()

    class TerminalService(UrlService):
        async def assist_with_batch_async(self, request, initial_batch):
            entered.set()
            if outcome == "FAILED":
                raise ValueError("synthetic private exception payload")
            await asyncio.Event().wait()

    manager = ReviewJobManager(TerminalService())
    try:
        with caplog.at_level("INFO", logger="forj_review_assistant"):
            created = manager.create(request(1))
            assert entered.wait(2)
            if outcome == "CANCELLED":
                manager.cancel(created.job_id)
                manager.cancel(created.job_id)
            assert manager.wait(created.job_id, 2)
            manager.close()
        lines = [record.getMessage() for record in caplog.records if "review job timing" in record.getMessage()]
        assert len(lines) == 1
        assert f"status={outcome}" in lines[0]
        assert "trace_id=" in lines[0] and f"job_id={created.job_id}" in lines[0]
        assert manager.get(created.job_id).status.value == outcome
    finally:
        manager.close()

"""本地异步审核任务管理。

主要职责：管理任务生命周期、进度快照、部分结果和过期清理。
修改日期：2026-08-26
"""

import logging
import os
import threading
import time
import uuid
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forj_review_service.businesses.material_policies import MaterialPolicy
from forj_review_service.businesses.material_presence import evaluate_presence
from forj_review_service.businesses.packs.model import BusinessExtensionPack
from forj_review_service.contracts.errors import ReviewJobNotFound
from forj_review_service.contracts.recognition import (
    MaterialCompletenessReport,
    RecognitionBatchResult,
)
from forj_review_service.contracts.review import (
    ImageInput,
    JobStatus,
    ReviewJobCreated,
    ReviewJobPhase,
    ReviewJobSnapshot,
    ReviewProgress,
    ReviewRequest,
    ReviewResponse,
)
from forj_review_service.observability.context import log_context
from forj_review_service.runtime.jobs.progress import batch_progress, scope_progress
from forj_review_service.runtime.scheduling.fair_scheduler import FairAsyncScheduler

JOB_TTL_SECONDS = 600.0
logger = logging.getLogger(__name__)


@dataclass
class _StoredJob:
    request: ReviewRequest
    snapshot: ReviewJobSnapshot
    done: threading.Event
    updated_at: float
    service: Any = None
    batch: RecognitionBatchResult = field(default_factory=RecognitionBatchResult)
    pending_count: int = 0
    finalize_started: bool = False
    cancelled: bool = False
    # 各阶段的单调时钟锚点，用于拆出「识别 / 汇总」各占多久。
    # 只用于日志，不进快照：加进 ReviewJobSnapshot 就是公开协议变更。
    timeline: dict[str, float] = field(default_factory=dict)
    terminal_logged: bool = False
    group_scopes: tuple[str, ...] = ()
    material_policy: MaterialPolicy | None = None


def _peak_rss_mb() -> int:
    """进程历史峰值驻留内存（MB）。读不到时返回 -1，不影响审核。

    取 `VmHWM`（high water mark）而不是 `VmRSS`：容器只有 1 GB，要的是
    「这一单最凶的时候占了多少」，当前值会在任务结束后回落，看不出风险。
    非 Linux（本地 Windows 调试）没有 /proc，返回 -1 让日志继续打。
    """
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return max(0, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        return -1
    return -1


class ReviewJobManager:
    """Run local review jobs outside request lifetimes and expose snapshots."""

    def __init__(
        self,
        review_service: Any,
        ttl_seconds: float = JOB_TTL_SECONDS,
        scheduler: FairAsyncScheduler | None = None,
        finalize_scheduler: FairAsyncScheduler | None = None,
    ) -> None:
        self.review_service = review_service
        self.ttl_seconds = ttl_seconds
        self._jobs: dict[str, _StoredJob] = {}
        self._lock = threading.Lock()
        self.scheduler = scheduler or FairAsyncScheduler(
            global_limit=int(os.getenv("REVIEW_MODEL_GLOBAL_CONCURRENCY", "12")),
            per_job_limit=int(os.getenv("REVIEW_MODEL_JOB_CONCURRENCY", "6")),
        )
        finalize_limit = int(os.getenv("REVIEW_FINALIZE_CONCURRENCY", "2"))
        self.finalize_scheduler = finalize_scheduler or FairAsyncScheduler(
            global_limit=finalize_limit,
            per_job_limit=1,
        )

    def close(self) -> None:
        """停止审核，在各调度循环清理服务连接后，再关闭调度器。"""
        with self._lock:
            running = [
                job_id
                for job_id, stored in self._jobs.items()
                if stored.snapshot.status is JobStatus.RUNNING
            ]
        for job_id in running:
            self.cancel(job_id)
        cleanup = getattr(self.review_service, "aclose", None)
        close_options = {"cleanup": cleanup} if cleanup is not None else {}
        try:
            self.scheduler.close(**close_options)
        finally:
            self.finalize_scheduler.close(**close_options)

    def create(self, request: ReviewRequest) -> ReviewJobCreated:
        return self.create_urls(request)

    def create_urls(self, request: ReviewRequest) -> ReviewJobCreated:
        """一次提交 URL 清单，全部图片进入公平队列，完成后自动汇总。"""
        self._cleanup()
        request.validate_images()
        request = request.model_copy(deep=True)
        job_id = str(uuid.uuid4())
        if not request.trace_id:
            request.trace_id = job_id
        created_at = datetime.now().astimezone().isoformat()
        now = time.monotonic()
        valid_images = [image for image in request.images if not image.collection_error]
        service = self.review_service
        pack = self._pack_for_request(request, service)
        stored = _StoredJob(
            request=request,
            service=service,
            snapshot=ReviewJobSnapshot(
                job_id=job_id,
                status=JobStatus.RUNNING,
                created_at=created_at,
                progress=ReviewProgress(
                    total_count=len(request.images), submitted_count=len(request.images)
                ),
                phase=ReviewJobPhase.RECOGNIZING,
            ),
            done=threading.Event(),
            updated_at=now,
            batch=RecognitionBatchResult(total_count=len(request.images)),
            pending_count=len(valid_images),
            timeline={"created_at": now},
            group_scopes=tuple(
                dict.fromkeys(
                    (
                        *(pack.scopes if pack else ()),
                        *(image.business_scope for image in request.images),
                    )
                )
            ),
            material_policy=pack.material_policy if pack else None,
        )
        for image in request.images:
            if image.collection_error:
                self._merge_batch(stored.batch, self._failed_image_batch(image))
        self._refresh_snapshot(stored)
        with self._lock:
            self._jobs[job_id] = stored
        logger.info(
            "review job created job_id=%s trace_id=%s images=%d collected_failed=%d",
            job_id,
            request.trace_id,
            len(request.images),
            len(request.images) - len(valid_images),
        )
        try:
            for image in valid_images:
                future = self.scheduler.submit(
                    job_id,
                    lambda current=image: self._extract_image_async(job_id, request, current),
                )
                future.add_done_callback(
                    lambda completed, current=image: self._image_finished(
                        job_id, current, completed
                    )
                )
            with self._lock:
                should_finalize = self._begin_finalize_locked(stored)
            if should_finalize:
                self._start_finalize(job_id)
        except Exception:
            self.cancel(job_id)
            raise
        return ReviewJobCreated(job_id=job_id, created_at=created_at)

    def cancel(self, job_id: str) -> ReviewJobSnapshot:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None:
                raise ReviewJobNotFound(job_id)
            if stored.snapshot.status is not JobStatus.RUNNING:
                return stored.snapshot.model_copy(deep=True)
            stored.cancelled = True
            stored.snapshot.status = JobStatus.CANCELLED
            stored.snapshot.phase = ReviewJobPhase.CANCELLED
            stored.snapshot.message = "审核任务已取消"
            stored.updated_at = time.monotonic()
            stored.timeline["terminal_at"] = stored.updated_at
            self._log_job_timing(stored)
            stored.done.set()
            snapshot = stored.snapshot.model_copy(deep=True)
        self.scheduler.cancel(job_id)
        self.finalize_scheduler.cancel(job_id)
        return snapshot

    def get(self, job_id: str) -> ReviewJobSnapshot:
        self._cleanup()
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None:
                raise ReviewJobNotFound(job_id)
            return stored.snapshot.model_copy(deep=True)

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            stored = self._jobs.get(job_id)
        return bool(stored and stored.done.wait(timeout))

    async def _extract_image_async(
        self, job_id: str, request: ReviewRequest, image: ImageInput
    ) -> RecognitionBatchResult:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is not None:
                stored.timeline.setdefault("recognize_started_at", time.monotonic())
        with log_context(job_id=job_id, trace_id=request.trace_id, image_id=self._image_id(image)):
            return await (stored.service if stored else self.review_service).extract_image_async(
                request, image
            )

    def _image_finished(
        self,
        job_id: str,
        image: ImageInput,
        future: Future[RecognitionBatchResult],
    ) -> None:
        if future.cancelled():
            return
        try:
            batch = future.result()
        except CancelledError:
            return
        except BaseException as exc:
            logger.exception(
                "URL image failed: job_id=%s image_id=%s error_type=%s",
                job_id,
                self._image_id(image),
                type(exc).__name__,
            )
            batch = self._failed_image_batch(image, "模型识别任务异常")

        should_finalize = False
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None or stored.cancelled:
                return
            self._merge_batch(stored.batch, batch)
            stored.pending_count = max(0, stored.pending_count - 1)
            stored.updated_at = time.monotonic()
            stored.timeline["last_done_at"] = stored.updated_at
            self._refresh_snapshot(stored)
            should_finalize = self._begin_finalize_locked(stored)
        if should_finalize:
            self._start_finalize(job_id)

    def _begin_finalize_locked(self, stored: _StoredJob) -> bool:
        if stored.pending_count == 0 and not stored.finalize_started and not stored.cancelled:
            stored.finalize_started = True
            stored.timeline["finalize_queued_at"] = time.monotonic()
            stored.snapshot.phase = ReviewJobPhase.FINALIZING
            return True
        return False

    def _start_finalize(self, job_id: str) -> None:
        self.finalize_scheduler.submit(
            job_id,
            lambda: self._finalize_with_context(job_id),
        )

    async def _finalize_with_context(self, job_id: str) -> None:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None:
                return
            trace_id = stored.request.trace_id
        with log_context(job_id=job_id, trace_id=trace_id, image_id="-"):
            await self._finalize_async(job_id)

    async def _finalize_async(self, job_id: str) -> None:
        with self._lock:
            stored = self._jobs.get(job_id)
            if stored is None or stored.cancelled:
                return
            request = stored.request.model_copy(deep=True)
            batch = stored.batch.model_copy(deep=True)
            stored.timeline["finalize_started_at"] = time.monotonic()
        try:
            response, final_batch = await stored.service.assist_with_batch_async(
                request,
                initial_batch=batch,
            )
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None or current.cancelled:
                    return
                current.batch = final_batch.model_copy(deep=True)
            self._update_snapshot(stored, response, final_batch, final=True)
        except Exception as exc:
            logger.exception(
                "URL review finalization failed: job_id=%s error_type=%s",
                job_id,
                type(exc).__name__,
            )
            with self._lock:
                if not stored.cancelled:
                    stored.snapshot.status = JobStatus.FAILED
                    stored.snapshot.phase = ReviewJobPhase.FAILED
                    stored.snapshot.message = f"审核任务失败：{type(exc).__name__}"
                    stored.updated_at = time.monotonic()
        finally:
            # 任务快照和模型结果保留到 TTL 到期。
            with self._lock:
                current = self._jobs.get(job_id)
                if current is not None:
                    current.updated_at = time.monotonic()
                    current.timeline["finalize_done_at"] = current.updated_at
                    current.timeline["terminal_at"] = current.updated_at
                    # 先记日志再放行等待者：`wait()` 一返回调用方就会去读日志，
                    # 顺序反了会让"任务已完成但 timing 行还没出现"的竞态成立。
                    self._log_job_timing(current)
                    current.done.set()

    def _log_job_timing(self, stored: _StoredJob) -> None:
        if stored.terminal_logged or stored.snapshot.status is JobStatus.RUNNING:
            return
        stored.terminal_logged = True
        timeline = stored.timeline

        def span(start: str, end: str) -> int:
            if start not in timeline or end not in timeline:
                return -1
            return max(0, int((timeline[end] - timeline[start]) * 1000))

        logger.info(
            "review job timing job_id=%s trace_id=%s status=%s images=%d completed=%d failed=%d timed_out=%d retries=%d recognize_wait_ms=%d recognize_ms=%d finalize_wait_ms=%d finalize_ms=%d total_ms=%d peak_rss_mb=%d",
            stored.snapshot.job_id,
            stored.request.trace_id,
            stored.snapshot.status.value,
            stored.batch.total_count,
            stored.batch.completed_count,
            stored.batch.failed_count,
            stored.batch.timed_out_count,
            stored.batch.retry_summary.total_retries,
            span("created_at", "recognize_started_at"),
            span("recognize_started_at", "last_done_at"),
            span("finalize_queued_at", "finalize_started_at"),
            span("finalize_started_at", "finalize_done_at"),
            span("created_at", "terminal_at"),
            _peak_rss_mb(),
        )

    def _refresh_snapshot(self, stored: _StoredJob) -> None:
        presence = evaluate_presence(stored.request, stored.material_policy, stored.batch)
        if presence is not None:
            stored.snapshot.material_completeness = MaterialCompletenessReport(
                phase="COLLECTED",
                status="UNCERTAIN",
                presence=presence,
            )
        stored.snapshot.failed_image_ids = list(dict.fromkeys(stored.batch.failed_image_ids))
        stored.snapshot.timed_out_image_ids = list(dict.fromkeys(stored.batch.timed_out_image_ids))
        stored.snapshot.progress = batch_progress(stored.batch, len(stored.request.images))
        stored.snapshot.groups = scope_progress(
            stored.request,
            stored.batch,
            stored.group_scopes,
            final=False,
        )

    def _pack_for_request(
        self, request: ReviewRequest, service=None
    ) -> BusinessExtensionPack | None:
        registry = getattr(service or self.review_service, "registry", None)
        if registry is None:
            raise ValueError("审核执行器必须提供当前数据库配置注册表")
        return registry.resolve(
            request.business_type,
            request.region,
            request.profile_version,
            page_id=request.page_id,
        ).pack_declaration

    @staticmethod
    def _image_id(image: ImageInput) -> str:
        return str(image.image_id or image.index)

    @classmethod
    def _failed_image_batch(
        cls,
        image: ImageInput,
        reason: str | None = None,
    ) -> RecognitionBatchResult:
        image_id = cls._image_id(image)
        return RecognitionBatchResult(
            total_count=1,
            failed_count=1,
            failed_image_ids=[image_id],
            limitations=[reason or f"图片 {image_id} 内容不可用"],
        )

    @staticmethod
    def _merge_batch(target: RecognitionBatchResult, incoming: RecognitionBatchResult) -> None:
        target.observations.extend(incoming.observations)
        target.limitations.extend(incoming.limitations)
        target.confidences.extend(incoming.confidences)
        target.completed_count += incoming.completed_count
        target.failed_count += incoming.failed_count
        target.timed_out_count += incoming.timed_out_count
        target.completed_image_ids.extend(incoming.completed_image_ids)
        target.failed_image_ids.extend(incoming.failed_image_ids)
        target.timed_out_image_ids.extend(incoming.timed_out_image_ids)
        target.recognized_documents.extend(incoming.recognized_documents)
        target.document_classifications.extend(incoming.document_classifications)
        target.retry_summary.attempts.extend(incoming.retry_summary.attempts)

    def _update_snapshot(
        self,
        stored: _StoredJob,
        response: ReviewResponse,
        batch: RecognitionBatchResult,
        *,
        final: bool,
    ) -> None:
        progress = batch_progress(batch, stored.snapshot.progress.submitted_count)
        groups = scope_progress(stored.request, batch, stored.group_scopes, final=final)
        processed_count = (
            progress.completed_count + progress.failed_count + progress.timed_out_count
        )
        if final:
            status = (
                JobStatus.PARTIAL
                if processed_count < progress.total_count
                or progress.failed_count
                or progress.timed_out_count
                else JobStatus.COMPLETED
            )
        else:
            status = JobStatus.RUNNING
        with self._lock:
            if stored.cancelled:
                return
            stored.snapshot.status = status
            stored.snapshot.progress = progress
            stored.snapshot.failed_image_ids = list(dict.fromkeys(batch.failed_image_ids))
            stored.snapshot.timed_out_image_ids = list(dict.fromkeys(batch.timed_out_image_ids))
            stored.snapshot.groups = groups
            stored.snapshot.result = response
            stored.snapshot.material_completeness = response.material_completeness
            stored.snapshot.retry_summary = response.retry_summary
            stored.snapshot.phase = (
                ReviewJobPhase.COMPLETED if final else ReviewJobPhase.RECOGNIZING
            )
            stored.updated_at = time.monotonic()

    def _cleanup(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        with self._lock:
            expired = [key for key, value in self._jobs.items() if value.updated_at < cutoff]
            for key in expired:
                del self._jobs[key]
        for key in expired:
            self.scheduler.cancel(key)
            self.finalize_scheduler.cancel(key)

"""从已完成的识别批次生成进度视图；不持有队列、锁或任务状态。"""

from collections.abc import Sequence

from forj_review_service.contracts.recognition import RecognitionBatchResult
from forj_review_service.contracts.review import (
    JobStatus,
    ReviewGroupProgress,
    ReviewProgress,
    ReviewRequest,
)


def batch_progress(batch: RecognitionBatchResult, submitted_count: int) -> ReviewProgress:
    return ReviewProgress(
        total_count=batch.total_count,
        submitted_count=submitted_count,
        completed_count=batch.completed_count,
        failed_count=batch.failed_count,
        timed_out_count=batch.timed_out_count,
    )


def scope_progress(
    request: ReviewRequest, batch: RecognitionBatchResult, scopes: Sequence[str], *, final: bool,
) -> dict[str, ReviewGroupProgress]:
    completed_ids = set(batch.completed_image_ids)
    failed_ids = set(batch.failed_image_ids)
    timed_out_ids = set(batch.timed_out_image_ids)
    groups = {}
    for scope in scopes:
        ids = {str(image.image_id or image.index) for image in request.images
               if image.business_scope == scope}
        completed = len(ids & completed_ids)
        failed = len(ids & failed_ids)
        timed_out = len(ids & timed_out_ids)
        processed = completed + failed + timed_out
        if not ids or processed == len(ids):
            status = JobStatus.PARTIAL if failed or timed_out else JobStatus.COMPLETED
        elif final or processed:
            status = JobStatus.PARTIAL
        else:
            status = JobStatus.RUNNING
        groups[scope] = ReviewGroupProgress(
            status=status, total_count=len(ids), completed_count=completed,
            failed_count=failed, timed_out_count=timed_out,
        )
    return groups

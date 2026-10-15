/**
 * 功能：展示审核任务进度和分组状态。
 * 职责边界：只消费任务快照，不修改任务状态。
 * 修改日期：2026-08-26
 */

import type { PageData, ReviewJobSnapshot } from "../../../shared/contracts/review";

export function ReviewProgress({ job, pageData, onFocusImage }: {
  job: ReviewJobSnapshot;
  pageData?: PageData | null;
  onFocusImage?: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
}) {
  const processed = job.progress.completed_count + job.progress.failed_count + job.progress.timed_out_count;
  const percent = job.progress.total_count
    ? Math.min(100, Math.round((processed / job.progress.total_count) * 100))
    : job.phase === "FINALIZING" || job.phase === "COMPLETED" ? 100 : 0;
  const phaseText = {
    PREPARING: "正在准备审核任务",
    RECOGNIZING: "正在识别图片",
    FINALIZING: "正在生成审核结论",
    COMPLETED: job.status === "PARTIAL" ? "审核部分完成，需人工复核" : "审核已完成",
    FAILED: "审核失败",
    CANCELLED: "审核已取消",
  }[job.phase];
  const failedIds = new Set(job.failed_image_ids ?? []);
  const timedOutIds = new Set(job.timed_out_image_ids ?? []);
  const affectedImages = (pageData?.images ?? []).filter((image) => {
    const id = String(image.imageId || image.index);
    return image.collectionError || failedIds.has(id) || timedOutIds.has(id);
  });
  return (
    <div className="review-progress">
      <div className="review-progress-heading"><strong>审核进度</strong><span>{phaseText}</span></div>
      <div
        className="review-progress-track"
        role="progressbar"
        aria-label="审核图片处理进度"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
      >
        <div className="review-progress-bar" style={{ width: `${percent}%` }} />
      </div>
      <small>图片已处理 {processed}/{job.progress.total_count} · 识别成功 {job.progress.completed_count} 张</small>
      {job.progress.failed_count > 0 || job.progress.timed_out_count > 0 ? (
        <small>失败 {job.progress.failed_count} 张，超时 {job.progress.timed_out_count} 张</small>
      ) : null}
      {job.progress.failed_count > 0 || job.progress.timed_out_count > 0 ? (
        <small>请刷新原页面后重新审核；仍未完成的材料请查看原图人工核对。</small>
      ) : null}
      {affectedImages.length ? <ul className="review-progress-failures" aria-label="未完成识别的材料">
        {affectedImages.map((image) => {
          const id = String(image.imageId || image.index);
          const title = image.alt || image.groupTitle || image.group || "材料";
          const status = image.collectionError ? "暂时无法读取" : timedOutIds.has(id) ? "识别超时" : "识别失败";
          return <li key={id}>
            <span>第 {image.index + 1} 张 · {title}：{status}</span>
            {image.imageId && onFocusImage ? <button type="button" className="evidence-focus" onClick={() => void onFocusImage(image.imageId as string)}>查看原图</button> : null}
          </li>;
        })}
      </ul> : null}
    </div>
  );
}

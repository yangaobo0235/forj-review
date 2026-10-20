/**
 * 功能：封装审核任务 HTTP 请求和状态错误映射。
 * 职责边界：不保存界面状态，不决定重试界面。
 * 修改日期：2026-08-26
 */

import type {
  PageData,
  ReviewJobCreated,
  ReviewJobSnapshot,
} from "../../../shared/contracts/review";

import { reviewApiBaseUrl } from "../../../shared/review-api-url.ts";
export { reviewApiBaseUrl, localReviewApiBaseUrl, publicReviewApiBaseUrl, resolveReviewApiBaseUrl } from "../../../shared/review-api-url.ts";

export type Fetcher = typeof fetch;

async function reviewRequest<T>(url: string, init: RequestInit | undefined, fetcher: Fetcher, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const cancel = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => controller.abort(new DOMException("审核请求超时，请检查网络后重新审核", "TimeoutError")), 15_000);
  let rejectAbort: (reason: unknown) => void = () => {};
  const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  const onAbort = () => rejectAbort(controller.signal.reason);
  controller.signal.addEventListener("abort", onAbort, { once: true });
  try {
    controller.signal.throwIfAborted();
    const request = async () => {
      let response: Response;
      try {
        response = await fetcher(url, { ...init, signal: controller.signal });
      } catch (cause) {
        if (controller.signal.aborted) throw controller.signal.reason;
        throw new Error("无法连接审核服务，请检查网络后重新审核；仍失败请联系管理员", { cause });
      }
      if (response.status === 404 && init?.method !== "POST") {
        throw new Error("本次审核任务已失效，请重新审核；仍失败请联系管理员");
      }
      if (!response.ok) throw requestFailure(response.status);
      return readReviewResponse<T>(response);
    };
    return await Promise.race([request(), aborted]);
  } catch (cause) {
    if (signal?.aborted) throw signal.reason;
    throw cause;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", onAbort);
  }
}

function requestFailure(status: number): Error {
  // 状态码保留在开发日志中，操作人员看到可执行的处理建议。
  console.warn(`[ForjReview] 审核请求失败，HTTP ${status}`);
  if (status === 401 || status === 403) {
    return new Error("暂时无法使用审核服务，请联系管理员检查访问权限");
  }
  if (status === 400 || status === 422) {
    return new Error("当前页面资料暂时无法用于审核，请刷新原页面后重新审核；仍失败请联系管理员");
  }
  if (status === 429) return new Error("审核服务繁忙，请稍后重新审核");
  return new Error("审核服务暂时不可用，请稍后重新审核；仍失败请联系管理员");
}

async function readReviewResponse<T>(response: Response): Promise<T> {
  try {
    return await response.json() as T;
  } catch (cause) {
    throw new Error("审核服务返回的结果暂时无法读取，请重新审核；仍失败请联系管理员", { cause });
  }
}

/** 一次提交 DOM 图片地址；后端立即调度模型，不读取或上传图片正文。 */
export async function createUrlReviewJob(
  data: PageData,
  fetcher: Fetcher = fetch,
  signal?: AbortSignal,
): Promise<ReviewJobCreated> {
  const images = data.images.map((image) => {
    const metadata = {
      index: image.index, src: image.src, imageId: image.imageId,
      group: image.group, categoryHint: image.categoryHint, businessScope: image.businessScope,
      groupTitle: image.groupTitle, groupOrder: image.groupOrder, documentTypeHint: image.documentTypeHint,
      identitySidesHint: image.identitySidesHint,
      alt: image.alt, pagePosition: image.pagePosition, collectionError: image.collectionError,
    };
    if (!metadata.collectionError && !/^https?:\/\//i.test(metadata.src || "")) {
      metadata.collectionError = "这份材料暂时无法读取，请刷新原页面后重新审核；仍失败请查看原图人工核对";
    }
    return metadata;
  });
  return reviewRequest<ReviewJobCreated>(`${reviewApiBaseUrl}/api/review/jobs/urls`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...data, images }),
  }, fetcher, signal);
}

export async function cancelReviewJob(jobId: string, fetcher: Fetcher = fetch): Promise<void> {
  try {
    await reviewRequest(`${reviewApiBaseUrl}/api/review/jobs/${jobId}`, { method: "DELETE" }, fetcher);
  } catch (reason) {
    // 取消属于善后动作，失败不能覆盖已取得的结论或产生未处理的 Promise 拒绝。
    console.warn("[ForjReview] 审核任务取消失败", reason instanceof Error ? reason.name : "request_failed");
  }
}

/** 返回任务最新快照；任务不存在时提供区别于一般请求失败的错误。 */
export async function fetchReviewJob(
  jobId: string,
  fetcher: Fetcher = fetch,
  signal?: AbortSignal,
): Promise<ReviewJobSnapshot> {
  return reviewRequest<ReviewJobSnapshot>(`${reviewApiBaseUrl}/api/review/jobs/${jobId}`, undefined, fetcher, signal);
}

export function completionNotice(
  snapshot: Pick<ReviewJobSnapshot, "status"> & Partial<Pick<ReviewJobSnapshot, "result" | "progress">>,
): string {
  if (snapshot.status === "RUNNING") {
    return snapshot.result
      ? "已等待 120 秒，已停止等待并申请取消本轮任务，当前结果仅供参考；未完成的材料请查看原图人工核对或重新审核"
      : "已等待 120 秒，已停止等待并申请取消本轮任务，审核尚未生成结论；请重新审核或查看原图人工核对";
  }
  if (snapshot.status === "PARTIAL") {
    const failed = snapshot.progress?.failed_count ?? 0;
    const timedOut = snapshot.progress?.timed_out_count ?? 0;
    const details = [failed ? `失败 ${failed} 张` : "", timedOut ? `超时 ${timedOut} 张` : ""].filter(Boolean).join("，");
    return `部分材料未完成识别${details ? `（${details}）` : ""}，已完成结果仅供参考；请刷新原页面后重新审核，仍未完成的材料请查看原图人工核对`;
  }
  return "";
}

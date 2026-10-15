export type PollableJobStatus = "RUNNING" | "PARTIAL" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface PollableJobSnapshot {
  status: PollableJobStatus;
  progress: {
    completed_count: number;
    total_count: number;
  };
}

interface PollOptions {
  timeoutMs?: number;
  intervalMs?: number;
  now?: () => number;
  signal?: AbortSignal;
}

const terminalStatuses = new Set<PollableJobStatus>(["PARTIAL", "COMPLETED", "FAILED", "CANCELLED"]);

export async function pollReviewJob<T extends PollableJobSnapshot>(
  fetchSnapshot: (signal: AbortSignal) => Promise<T>,
  onSnapshot: (snapshot: T) => void,
  options: PollOptions = {},
): Promise<T> {
  const timeoutMs = options.timeoutMs ?? 60_000;
  const intervalMs = options.intervalMs ?? 1_000;
  const now = options.now ?? Date.now;
  const startedAt = now();
  let latest: T | undefined;
  const controller = new AbortController();
  const cancel = () => controller.abort(options.signal?.reason);
  const timer = setTimeout(() => controller.abort(new DOMException("审核等待超时", "TimeoutError")), timeoutMs);
  options.signal?.addEventListener("abort", cancel, { once: true });
  if (options.signal?.aborted) cancel();
  let rejectAbort: (reason: unknown) => void = () => {};
  const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  const onAbort = () => rejectAbort(controller.signal.reason);
  controller.signal.addEventListener("abort", onAbort, { once: true });
  // Promise.race 安全处理不响应 AbortSignal 的调用方，同时防止晚到结果回写。
  try {
    controller.signal.throwIfAborted();
    while (now() - startedAt < timeoutMs) {
      latest = await Promise.race([fetchSnapshot(controller.signal), aborted]);
      controller.signal.throwIfAborted();
      onSnapshot(latest);
      if (terminalStatuses.has(latest.status)) return latest;
      if (intervalMs > 0) {
        await Promise.race([new Promise(resolve => setTimeout(resolve, intervalMs)), aborted]);
      }
    }
  } catch (reason) {
    if (options.signal?.aborted) throw reason;
    if (!controller.signal.aborted) throw reason;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", onAbort);
  }
  if (latest) return latest;
  throw new Error("审核任务未返回进度");
}

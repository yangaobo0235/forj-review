import { collectPageData } from "../api/collect-page";
import { executePageActions } from "../../../platform/extension/execute-page-actions";
/**
 * 功能：管理页面采集、任务轮询和部分结果生命周期。
 * 职责边界：不渲染审核结果，不解释后端规则；页面写入只通过受控动作触发。
 * 修改日期：2026-09-11
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cancelReviewJob,
  completionNotice,
  createUrlReviewJob,
  fetchReviewJob,
} from "../api/review-client";
import { pollReviewJob } from "../api/review-jobs";
import {
  applyPageFieldGroupValue,
  applyPageFieldValue,
  type PageFillResult,
} from "../../../platform/extension/page-fill-client";
import {
  focusReviewImage,
  type ImageFocusResult,
} from "../../../platform/extension/image-focus-client";
import { type BusinessChoice } from "./review-panel";
import type {
  PageData,
  ReviewJobSnapshot,
  ReviewResponse,
} from "../../../shared/contracts/review";

const reviewDeadlineMs = 120_000;
const reviewPollIntervalMs = 1_000;

export interface ReviewWorkflow {
  loading: boolean;
  review: ReviewResponse | null;
  job: ReviewJobSnapshot | null;
  pageData: PageData | null;
  error: string;
  notice: string;
  stageMessage: string;
  reset: () => void;
  startReview: () => Promise<void>;
  applyPageFieldValue: (
    field: string,
    value: string,
    expectedValue?: string | null,
  ) => Promise<PageFillResult>;
  applyPageFieldGroupValue: (
    fields: string[],
    value: string,
    expectedValues?: Record<string, string | null | undefined>,
  ) => Promise<PageFillResult>;
  focusOriginalImage: (imageId: string) => Promise<ImageFocusResult>;
}

/** 管理一次审核从页面采集到最终或部分结果的完整客户端生命周期。 */
export function useReviewWorkflow(
  businessSelection: BusinessChoice,
): ReviewWorkflow {
  const [loading, setLoading] = useState(false);
  const [review, setReview] = useState<ReviewResponse | null>(null);
  const [job, setJob] = useState<ReviewJobSnapshot | null>(null);
  const [pageData, setPageData] = useState<PageData | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [stageMessage, setStageMessage] = useState("");
  const activeRun = useRef<{
    controller: AbortController;
    jobId: string;
  } | null>(null);
  const preflightVersion = useRef(0);

  const stopActiveRun = useCallback(() => {
    const run = activeRun.current;
    activeRun.current = null;
    if (!run) return;
    run.controller.abort();
    if (run.jobId) void cancelReviewJob(run.jobId);
  }, []);

  useEffect(() => {
    stopActiveRun();
    let cancelled = false;
    const version = ++preflightVersion.current;
    void collectPageData(businessSelection)
      .then((data) => {
        if (cancelled || version !== preflightVersion.current) return;
        setPageData(data);
        if (data.businessDetectionError) {
          setError(data.businessDetectionError);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled && version === preflightVersion.current) {
          setError(reason instanceof Error ? reason.message : "页面采集失败");
        }
      });
    return () => {
      cancelled = true;
      stopActiveRun();
    };
  }, [businessSelection, stopActiveRun]);

  const reset = useCallback(() => {
    ++preflightVersion.current;
    stopActiveRun();
    setLoading(false);
    setReview(null);
    setJob(null);
    setPageData(null);
    setError("");
    setNotice("");
    setStageMessage("");
  }, [stopActiveRun]);

  const startReview = useCallback(async () => {
    // React 状态更新会批处理，ref 锁在同一事件周期内也能阻止重复提交。
    if (activeRun.current) return;
    ++preflightVersion.current;
    const run = { controller: new AbortController(), jobId: "" };
    activeRun.current = run;
    const isCurrent = () =>
      activeRun.current === run && !run.controller.signal.aborted;
    setLoading(true);
    setError("");
    setNotice("");
    setReview(null);
    setJob(null);
    setStageMessage("正在读取页面和图片清单……");
    let activeJobId = "";
    try {
      const data = await collectPageData(businessSelection);
      if (!isCurrent()) return;
      if (data.staleCollection) {
        // 被取代的采集：透出真实的过期原因，而不是退化为业务识别失败。
        throw new Error(
          data.collectionIssues?.[0] || "页面采集已过期，请重新采集",
        );
      }
      if (!data.businessType) {
        throw new Error(
          data.businessDetectionError || "无法识别当前审核业务，请人工选择",
        );
      }
      setPageData(data);
      // 采集阶段的问题必须写在面板上。`collectionIssues` 目前承载的是
      // 「候选资料超过 16 张，请确认是否漏审」——图片溢出意味着可能漏审，
      // 而它以前只进 Console，审核员看不到。结构化诊断仍留给开发者。
      if (data.collectionIssues?.length) {
        setNotice(data.collectionIssues.join("；"));
      }
      console.info(
        "[ReviewAgent][panel] page collected",
        data.collectionDiagnostics,
      );
      setStageMessage(
        `已发现 ${data.images.length} 张图片，正在创建审核任务……`,
      );
      const created = await createUrlReviewJob(data);
      activeJobId = created.job_id;
      run.jobId = created.job_id;
      if (!isCurrent()) {
        void cancelReviewJob(created.job_id);
        return;
      }
      setStageMessage("正在识别图片并生成审核结论……");
      const polling = pollReviewJob<ReviewJobSnapshot>(
        (signal) => fetchReviewJob(created.job_id, fetch, signal),
        (snapshot) => {
          if (!isCurrent()) return;
          setJob(snapshot);
          if (snapshot.result) setReview(snapshot.result);
        },
        {
          timeoutMs: reviewDeadlineMs,
          intervalMs: reviewPollIntervalMs,
          signal: run.controller.signal,
        },
      );
      const finalSnapshot = await polling;
      if (!isCurrent()) return;
      if (finalSnapshot.status === "FAILED") {
        console.warn("[ReviewAgent] 审核任务执行失败", finalSnapshot.message);
        throw new Error(
          "本次审核未能完成，请重新审核；仍失败请查看原图人工核对并联系管理员",
        );
      }
      if (finalSnapshot.status === "CANCELLED")
        throw new Error("审核任务已取消");
      if (finalSnapshot.status !== "RUNNING") activeJobId = "";
      const actionResults =
        finalSnapshot.status === "RUNNING"
          ? []
          : await executePageActions(
              finalSnapshot.result?.page_actions ?? [],
              data,
            );
      if (!isCurrent()) return;
      // 与采集阶段的提示合并，而不是互相覆盖：两条都是审核员需要看到的。
      if (actionResults.length) {
        setNotice((current) =>
          [current, ...actionResults].filter(Boolean).join("；"),
        );
      }
      // 等待上限不代表已有审核结论；完成提示也不能被采集或页面动作提示遮住。
      setNotice((current) =>
        [current, completionNotice(finalSnapshot)].filter(Boolean).join("；"),
      );
    } catch (reason: unknown) {
      if (activeJobId) {
        void cancelReviewJob(activeJobId);
        activeJobId = "";
      }
      if (!isCurrent()) return;
      setError(
        reason instanceof Error ? reason.message : "审核辅助服务调用失败",
      );
    } finally {
      if (activeRun.current === run) {
        // 客户端停止等待后不留下无法继续观察的后台任务。
        if (activeJobId) void cancelReviewJob(activeJobId);
        activeRun.current = null;
        setLoading(false);
        setStageMessage("");
      }
    }
  }, [businessSelection]);

  const applyFieldValue = useCallback(
    async (field: string, value: string, expectedValue?: string | null) => {
      if (!pageData) return { ok: false, message: "没有找到原审核页面" };
      const result = await applyPageFieldValue(
        { field, value, expectedValue },
        {
          tabId: pageData.sourceTabId,
          pageUrl: pageData.pageUrl,
          pageInstanceId: pageData.pageInstanceId,
          pageFingerprint: pageData.pageFingerprint,
          collectionId: pageData.collectionId,
        },
      );
      if (
        result.ok ||
        (result.code === "FIELD_VALUE_CHANGED" && result.currentValue != null)
      )
        setPageData((current) =>
          current?.collectionId === pageData.collectionId
            ? {
                ...current,
                pageFields: {
                  ...current.pageFields,
                  [field]: result.ok
                    ? (result.actions?.[0]?.value ?? value)
                    : (result.currentValue as string),
                },
              }
            : current,
        );
      return result;
    },
    [pageData],
  );

  const applyFieldGroupValue = useCallback(
    async (
      fields: string[],
      value: string,
      expectedValues?: Record<string, string | null | undefined>,
    ) => {
      if (!pageData) return { ok: false, message: "没有找到原审核页面" };
      const expected =
        expectedValues ||
        Object.fromEntries(
          fields.map((field) => [field, pageData.pageFields[field] ?? null]),
        );
      const result = await applyPageFieldGroupValue(
        { fields, value, expectedValues: expected },
        {
          tabId: pageData.sourceTabId,
          pageUrl: pageData.pageUrl,
          pageInstanceId: pageData.pageInstanceId,
          pageFingerprint: pageData.pageFingerprint,
          collectionId: pageData.collectionId,
        },
      );
      if (result.ok) {
        setPageData((current) =>
          current?.collectionId === pageData.collectionId
            ? {
                ...current,
                pageFields: {
                  ...current.pageFields,
                  ...Object.fromEntries(fields.map((field) => [field, value])),
                },
              }
            : current,
        );
      }
      return result;
    },
    [pageData],
  );

  const focusOriginalImage = useCallback(
    async (imageId: string) => {
      if (!pageData) {
        const failure = { ok: false, error: "没有找到原审核页面" };
        setNotice(failure.error);
        return failure;
      }
      // 无论成功还是失败都把结果交回调用方：面板顶部的提示离字段卡片很远，
      // 只在那里报错等于"点了没反应"。详情卡片会把结果显示在卡片旁边。
      const response = await focusReviewImage(imageId, pageData);
      if (!response?.ok) setNotice(response?.error || "原图已变化，请重新采集");
      return response ?? { ok: false, error: "原图已变化，请重新采集" };
    },
    [pageData],
  );

  return {
    loading,
    review,
    job,
    pageData,
    error,
    notice,
    stageMessage,
    reset,
    startReview,
    applyPageFieldValue: applyFieldValue,
    applyPageFieldGroupValue: applyFieldGroupValue,
    focusOriginalImage,
  };
}

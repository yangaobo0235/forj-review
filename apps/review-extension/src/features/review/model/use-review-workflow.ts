import { collectPageData } from "../api/collect-page";
import { fillVehicleModel } from "../../../platform/extension/vehicle-model-client";
import type { ModelFillResult } from "../../../shared/contracts/vehicle-model";
import { logDiagnostic } from "../../../shared/diagnostics";
import { executePageActions } from "../../../platform/extension/execute-page-actions";
/**
 * 功能：管理页面采集、任务轮询和部分结果生命周期。
 * 职责边界：不渲染审核结果，不解释后端规则；页面写入只通过受控动作触发。
 * 修改日期：2026-09-11
 */
import { shallowReactive, watch, toValue, type MaybeRefOrGetter } from "vue";
import { cancelReviewJob, completionNotice, createUrlReviewJob, fetchReviewJob, } from "../api/review-client";
import { pollReviewJob } from "../api/review-jobs";
import { applyPageFieldGroupValue, applyPageFieldValue, type PageFillResult, } from "../../../platform/extension/page-action-client";
import { focusReviewImage, type ImageFocusResult, } from "../../../platform/extension/image-focus-client";
import { type BusinessChoice } from "./review-panel";
import type { PageData, ReviewJobSnapshot, ReviewResponse, } from "../../../shared/contracts/review";
const reviewDeadlineMs = 120000;
const reviewPollIntervalMs = 1000;
export interface ReviewWorkflow {
    loading: boolean;
    review: ReviewResponse | null;
    job: ReviewJobSnapshot | null;
    pageData: PageData | null;
    error: string;
    notice: string;
    stageMessage: string;
    reset: () => void;
    refreshPage: () => Promise<void>;
    startReview: () => Promise<void>;
    applyPageFieldValue: (field: string, value: string, expectedValue?: string | null) => Promise<PageFillResult>;
    applyPageFieldGroupValue: (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => Promise<PageFillResult>;
    focusOriginalImage: (imageId: string) => Promise<ImageFocusResult>;
    applyVehicleModel: (searchId: string, modelId: string) => Promise<ModelFillResult>;
}
/** 管理一次审核从页面采集到最终或部分结果的完整客户端生命周期。 */
export function useReviewWorkflow(businessSelection: MaybeRefOrGetter<BusinessChoice>): ReviewWorkflow {
    const state = shallowReactive({ loading: false, review: null as ReviewResponse | null,
        job: null as ReviewJobSnapshot | null, pageData: null as PageData | null,
        error: "", notice: "", stageMessage: "" });
    let activeRun: {
        controller: AbortController;
        jobId: string;
    } | null = null;
    let preflightVersion = 0;
    const stopActiveRun = () => {
        const run = activeRun;
        activeRun = null;
        if (!run)
            return;
        run.controller.abort();
        if (run.jobId)
            void cancelReviewJob(run.jobId);
    };
    const refreshPage = async () => {
        stopActiveRun();
        const version = ++preflightVersion;
        state.pageData = null;
        state.review = null;
        state.job = null;
        state.error = "";
        state.notice = "";
        try {
            const data = await collectPageData(toValue(businessSelection));
            if (version !== preflightVersion) return;
            state.pageData = data;
            state.error = data.reviewServiceError || data.businessDetectionError || "";
        } catch (reason: unknown) {
            if (version === preflightVersion)
                state.error = reason instanceof Error ? reason.message : "页面采集失败";
        }
    };
    watch(() => toValue(businessSelection), (_selection, _previous, onCleanup) => {
        void refreshPage();
        onCleanup(() => { ++preflightVersion; stopActiveRun(); });
    }, { immediate: true, flush: "sync" });
    const reset = () => {
        ++preflightVersion;
        stopActiveRun();
        state.loading = false;
        state.review = null;
        state.job = null;
        state.pageData = null;
        state.error = "";
        state.notice = "";
        state.stageMessage = "";
    };
    const startReview = async () => {
        const selection = toValue(businessSelection);
        // 使用同步运行锁，阻止同一事件周期内重复创建审核任务。
        if (activeRun)
            return;
        ++preflightVersion;
        const run = { controller: new AbortController(), jobId: "" };
        activeRun = run;
        const isCurrent = () => activeRun === run && !run.controller.signal.aborted;
        state.loading = true;
        state.error = "";
        state.notice = "";
        state.review = null;
        state.job = null;
        state.stageMessage = "正在读取页面和图片清单……";
        let activeJobId = "";
        try {
            const data = await collectPageData(selection);
            if (!isCurrent())
                return;
            if (data.staleCollection) {
                // 被取代的采集：透出真实的过期原因，而不是退化为业务识别失败。
                throw new Error(data.collectionIssues?.[0] || "页面采集已过期，请重新采集");
            }
            if (!data.businessType) {
                throw new Error(data.businessDetectionError || "无法识别当前审核业务，请人工选择");
            }
            state.pageData = data;
            if (data.reviewServiceError) throw new Error(data.reviewServiceError);
            // 采集阶段的问题必须写在面板上。`collectionIssues` 目前承载的是
            // 「候选资料超过 16 张，请确认是否漏审」——图片溢出意味着可能漏审，
            // 而它以前只进 Console，审核员看不到。结构化诊断仍留给开发者。
            if (data.collectionIssues?.length) {
                state.notice = data.collectionIssues.join("；");
            }
            logDiagnostic("panel.collected", { collectionId: data.collectionId, imageCount: data.images.length, fieldCount: data.reviewFields?.length ?? 0 });
            state.stageMessage = `已发现 ${data.images.length} 张图片，正在创建审核任务……`;
            const created = await createUrlReviewJob(data);
            activeJobId = created.job_id;
            logDiagnostic("job.created", { collectionId: data.collectionId, job_id: created.job_id });
            run.jobId = created.job_id;
            if (!isCurrent()) {
                void cancelReviewJob(created.job_id);
                return;
            }
            state.stageMessage = "正在识别图片并生成审核结论……";
            const polling = pollReviewJob<ReviewJobSnapshot>((signal) => fetchReviewJob(created.job_id, fetch, signal), (snapshot) => {
                if (!isCurrent())
                    return;
                state.job = snapshot;
                if (snapshot.result)
                    state.review = snapshot.result;
            }, {
                timeoutMs: reviewDeadlineMs,
                intervalMs: reviewPollIntervalMs,
                signal: run.controller.signal,
            });
            const finalSnapshot = await polling;
            logDiagnostic("job.completed", { job_id: created.job_id, status: finalSnapshot.status });
            if (!isCurrent())
                return;
            if (finalSnapshot.status === "FAILED") {
                logDiagnostic("job.failed", { job_id: created.job_id, status: finalSnapshot.status });
                throw new Error("本次审核未能完成，请重新审核；仍失败请查看原图人工核对并联系管理员");
            }
            if (finalSnapshot.status === "CANCELLED")
                throw new Error("审核任务已取消");
            if (finalSnapshot.status !== "RUNNING")
                activeJobId = "";
            const actionResults = finalSnapshot.status === "RUNNING"
                ? []
                : await executePageActions(finalSnapshot.result?.page_actions ?? [], data);
            if (!isCurrent())
                return;
            // 与采集阶段的提示合并，而不是互相覆盖：两条都是审核员需要看到的。
            if (actionResults.length) {
                state.notice = ((current) => [current, ...actionResults].filter(Boolean).join("；"))(state.notice);
            }
            // 等待上限不代表已有审核结论；完成提示也不能被采集或页面动作提示遮住。
            state.notice = ((current) => [current, completionNotice(finalSnapshot)].filter(Boolean).join("；"))(state.notice);
        }
        catch (reason: unknown) {
            if (activeJobId) {
                void cancelReviewJob(activeJobId);
                activeJobId = "";
            }
            if (!isCurrent())
                return;
            state.error = reason instanceof Error ? reason.message : "审核辅助服务调用失败";
        }
        finally {
            if (activeRun === run) {
                // 客户端停止等待后不留下无法继续观察的后台任务。
                if (activeJobId)
                    void cancelReviewJob(activeJobId);
                activeRun = null;
                state.loading = false;
                state.stageMessage = "";
            }
        }
    };
    const applyFieldValue = async (field: string, value: string, expectedValue?: string | null) => {
        const pageData = state.pageData;
        if (!pageData)
            return { ok: false, message: "没有找到原审核页面" };
        const result = await applyPageFieldValue({ field, value, expectedValue }, {
            tabId: pageData.sourceTabId,
            pageUrl: pageData.pageUrl,
            pageInstanceId: pageData.pageInstanceId,
            pageFingerprint: pageData.pageFingerprint,
            collectionId: pageData.collectionId,
        });
        if (result.ok ||
            (result.code === "FIELD_VALUE_CHANGED" && result.currentValue != null))
            state.pageData = ((current) => current?.collectionId === pageData.collectionId
                ? {
                    ...current,
                    pageFields: {
                        ...current.pageFields,
                        [field]: result.ok
                            ? (result.actions?.[0]?.value ?? value)
                            : (result.currentValue as string),
                    },
                }
                : current)(state.pageData);
        return result;
    };
    const applyFieldGroupValue = async (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => {
        const pageData = state.pageData;
        if (!pageData)
            return { ok: false, message: "没有找到原审核页面" };
        const expected = expectedValues ||
            Object.fromEntries(fields.map((field) => [field, pageData.pageFields[field] ?? null]));
        const result = await applyPageFieldGroupValue({ fields, value, expectedValues: expected }, {
            tabId: pageData.sourceTabId,
            pageUrl: pageData.pageUrl,
            pageInstanceId: pageData.pageInstanceId,
            pageFingerprint: pageData.pageFingerprint,
            collectionId: pageData.collectionId,
        });
        if (result.ok) {
            state.pageData = ((current) => current?.collectionId === pageData.collectionId
                ? {
                    ...current,
                    pageFields: {
                        ...current.pageFields,
                        ...Object.fromEntries(fields.map((field) => [field, value])),
                    },
                }
                : current)(state.pageData);
        }
        return result;
    };
    const focusOriginalImage = async (imageId: string) => {
        const pageData = state.pageData;
        if (!pageData) {
            const failure = { ok: false, error: "没有找到原审核页面" };
            state.notice = failure.error;
            return failure;
        }
        // 无论成功还是失败都把结果交回调用方：面板顶部的提示离字段卡片很远，
        // 只在那里报错等于"点了没反应"。详情卡片会把结果显示在卡片旁边。
        const response = await focusReviewImage(imageId, pageData);
        if (!response?.ok)
            state.notice = response?.error || "原图已变化，请重新采集";
        return response ?? { ok: false, error: "原图已变化，请重新采集" };
    };
    const applyVehicleModel = async (searchId: string, modelId: string): Promise<ModelFillResult> => {
        const page = state.pageData;
        if (!page) return {ok: false, message: "没有找到原审核页面"};
        const result = await fillVehicleModel(page, searchId, modelId);
        if (state.pageData?.collectionId === page.collectionId && result.actions?.length) {
            state.pageData = {...state.pageData, pageFields: {...state.pageData.pageFields,
                ...Object.fromEntries(result.actions.map(item => [item.field, item.value]))}};
        }
        return result;
    };
    return Object.assign(state, {
      reset, refreshPage, startReview, applyPageFieldValue: applyFieldValue,
      applyPageFieldGroupValue: applyFieldGroupValue, focusOriginalImage, applyVehicleModel,
    });
}

import "./EvidenceReviewWorkbench.css";
import { ReviewStepDetail } from "./ReviewStepDetail";
import {
  workbenchViews,
  fieldFromStep,
  isExternalTask,
  statusText,
  stepTitle,
  type View,
  type Decision,
} from "../model/workbench";
import { useCallback, useMemo, useState } from "react";

import {
  isBlockingPageFillFailure,
  type PageFillResult,
} from "../../../shared/contracts/page-fill";
import { isFieldFirstTaskVisible } from "../model/review-tasks";
import type {
  PageData,
  ReviewResponse,
  ReviewTask,
} from "../../../shared/contracts/review";

/**
 * 字段优先工作台：按后端给出的字段条目展示证据、结论和人工处置。
 *
 * 显示策略由后端业务声明投影，
 * 组件只解释证据、任务与操作，不依赖业务 ID 或配置版本。
 */
interface Props {
  readOnly?: boolean;
  review: ReviewResponse;
  pageData?: PageData | null;
  onFocusImage?: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
  onApplyPageFieldValue?: (
    field: string,
    value: string,
    expectedValue?: string | null,
  ) => Promise<PageFillResult>;
  onApplyPageFieldGroupValue?: (
    fields: string[],
    value: string,
    expectedValues?: Record<string, string | null | undefined>,
  ) => Promise<PageFillResult>;
  onRerun?: () => Promise<void>;
}

export function EvidenceReviewWorkbench(props: Props) {
  return <Workbench key={props.pageData?.collectionId} {...props} />;
}

function Workbench({
  review,
  pageData,
  readOnly = false,
  onFocusImage = async () => ({ ok: false, error: "原图定位未配置" }),
  onApplyPageFieldValue = async () => ({
    ok: false,
    message: "页面写回未配置",
  }),
  onApplyPageFieldGroupValue = async () => ({
    ok: false,
    message: "页面组合字段写回未配置",
  }),
  onRerun,
}: Props & { review: ReviewResponse }) {
  const steps = useMemo(
    () =>
      [...(review.review_tasks ?? [])].sort((a, b) => a.sequence - b.sequence),
    [review.review_tasks],
  );
  // Apply the presentation policy before deriving tabs, counts or selection,
  // so hidden backend compatibility tasks cannot leak into a secondary view.
  // 日期类规则结论已经由后端并进对应字段行（合并关系来自业务声明的
  // `field_check_bindings`），这里不再自己拿一张表去猜该藏哪条任务——
  // 前端持有那张表时，新增一个带日期规则的业务就要回来补一行。
  const displaySteps = steps.filter(isFieldFirstTaskVisible);
  const [view, setView] = useState<View>("PENDING");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [message, setMessage] = useState("");
  const [filledCount, setFilledCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [blockingIssue, setBlockingIssue] = useState("");
  const effectiveNeedsAction = (step: ReviewTask) =>
    step.requires_reviewer_action;
  const pending = displaySteps.filter(
    (step) => effectiveNeedsAction(step) && !decisions[step.step_id],
  );
  const visible =
    view === "PENDING"
      ? pending
      : view === "EXTERNAL"
        ? displaySteps.filter(isExternalTask)
        : displaySteps.filter((step) => step.category === "FIELD");
  const current =
    visible.find((step) => step.step_id === selectedId) ?? visible[0] ?? null;
  const choose = (step: ReviewTask, decision: Decision) => {
    setDecisions((previous) => ({ ...previous, [step.step_id]: decision }));
    setSelectedId(null);
    setMessage("已记录本项人工处置");
  };
  const handleWriteFailure = useCallback((reason: unknown) => {
    const failure =
      typeof reason === "object" && reason && "message" in reason
        ? (reason as Pick<PageFillResult, "message" | "code">)
        : { message: typeof reason === "string" ? reason : "页面回填失败" };
    setMessage(failure.message);
    if (isBlockingPageFillFailure(failure)) setBlockingIssue(failure.message);
  }, []);
  // 原图定位的结果就地显示：面板顶部的提示离字段卡片很远，只在那里报错
  // 等于"点了没反应"。
  const focus = async (imageId: string) => {
    setMessage("正在定位原图…");
    const result = await onFocusImage(imageId);
    setMessage(
      result?.ok
        ? "已定位到原图，页面已滚动并高亮该图；如未看到请检查原审核页面"
        : result?.error || "原图定位失败，请重新采集",
    );
  };
  const fill = async (step: ReviewTask, value: string) => {
    const field = fieldFromStep(step);
    if (!field || !pageData || busy || blockingIssue) return;
    const fields = step.page_target_fields?.length
      ? step.page_target_fields
      : [field];
    const expectedValues = Object.fromEntries(
      fields.map((targetField) => [
        targetField,
        pageData.pageFields[targetField] ?? null,
      ]),
    );
    setBusy(true);
    setMessage(
      fields.length > 1
        ? "正在联合回填并回读页面字段..."
        : "正在回填并回读页面字段...",
    );
    try {
      const result =
        fields.length > 1
          ? await onApplyPageFieldGroupValue(fields, value, expectedValues)
          : await onApplyPageFieldValue(field, value, expectedValues[field]);
      if (result.ok) {
        setFilledCount((count) => count + 1);
        setMessage(
          `${stepTitle(step)}已${fields.length > 1 ? "同时回填两个字段并" : ""}回读，已定位到页面字段；核对后请点击“标记人工复核”`,
        );
      } else handleWriteFailure(result);
    } catch (error) {
      handleWriteFailure(error);
    } finally {
      setBusy(false);
    }
  };
  const allDone = pending.length === 0;
  return (
    <section className="review-workbench" aria-label="字段证据审核工作台">
      <header className="workbench-summary">
        <div>
          <strong>字段证据审核</strong>
          <small>
            自动通过{" "}
            {displaySteps.filter((step) => !effectiveNeedsAction(step)).length}{" "}
            · 已人工处理 {Object.keys(decisions).length} · 待处理{" "}
            {pending.length}
          </small>
        </div>
        <b>
          {blockingIssue
            ? "页面已失效"
            : allDone
              ? "展示项目已处理"
              : `${pending.length} 项待处理`}
        </b>
      </header>
      {blockingIssue ? (
        <div className="workbench-blocked" role="alert">
          <strong>页面操作已停止</strong>
          <span>{blockingIssue}</span>
          {onRerun ? (
            <button type="button" onClick={() => void onRerun()}>
              重新采集并复核
            </button>
          ) : null}
        </div>
      ) : null}
      <nav className="workbench-tabs" aria-label="审核视图">
        {workbenchViews({
          pending: pending.length,
          fieldCount: displaySteps.filter((step) => step.category === "FIELD")
            .length,
          externalCount: displaySteps.filter(isExternalTask).length,
        }).map(([key, label]) => (
          <button
            type="button"
            aria-pressed={view === key}
            className={view === key ? "active" : ""}
            key={key}
            onClick={() => {
              setView(key);
              setSelectedId(null);
              setMessage("");
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="workbench-content">
        <nav className="workbench-index" aria-label="核验项目">
          {visible.map((step) => (
            <button
              type="button"
              aria-pressed={current?.step_id === step.step_id}
              className={current?.step_id === step.step_id ? "selected" : ""}
              key={step.step_id}
              onClick={() => {
                setSelectedId(step.step_id);
                setMessage("");
              }}
            >
              <span>{stepTitle(step)}</span>
              <b className={`status-${step.result_status.toLowerCase()}`}>
                {decisions[step.step_id]
                  ? "已处理"
                  : statusText[step.result_status]}
              </b>
            </button>
          ))}
        </nav>
        <div className="workbench-detail">
          {current ? (
            <ReviewStepDetail
              step={current}
              pageData={pageData}
              qrChecks={review.qr_checks}
              disabled={readOnly || busy || Boolean(blockingIssue)}
              onFocusImage={focus}
              onFill={fill}
              onChoose={choose}
            />
          ) : (
            <div className="workbench-empty">
              <strong>
                {allDone ? "当前展示项目已处理" : "当前没有待处理项目"}
              </strong>
              <p>可切换到全部字段查看完整核验记录。</p>
            </div>
          )}
          {message ? (
            <p className="workbench-message" role="status">
              {message}
            </p>
          ) : null}
        </div>
      </div>
      {filledCount > 0 && onRerun && !blockingIssue ? (
        <div className="final-review-action">
          <span>页面已发生 {filledCount} 项回填</span>
          <button type="button" onClick={() => void onRerun()}>
            重新采集并最终复核
          </button>
        </div>
      ) : null}
    </section>
  );
}

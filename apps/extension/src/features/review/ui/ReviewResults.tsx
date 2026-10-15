/**
 * 功能：按业务 Profile 分流审核结果展示。
 * 职责边界：已注册业务 Profile 走字段优先助手；未配置业务
 * 继续使用现有结果界面和行为（LegacyReviewResults，保持原样）。
 * 修改日期：2026-09-11
 */

import {
  comparisonEvidence,
  evidenceHighlightPlans,
  evidencePresentation,
  type EvidenceHighlightPlan,
} from "../presentation/evidence";
import { exceptionComparisons, exceptionSections, type ExceptionFilter } from "../presentation/exception";
import { qrCheckPresentation } from "../presentation/qr";
import { fieldLabel, groupStatusLabel, statusLabel } from "../model/review-panel";
import type { EvidenceFact, FieldComparison, JobStatus, PageData, ReviewJobSnapshot, ReviewResponse } from "../../../shared/contracts/review";
import { diffValue, diffValueByPosition } from "../presentation/value-diff";
import { ReviewAdvice } from "./ReviewAdvice";
import { ReviewTaskList } from "./ReviewTaskList";
import { resolveWorkbenchRenderer } from "./workbench-renderers";
import type { PageFillResult } from "../../../platform/extension/page-fill-client";

interface ReviewResultsProps {
  review: ReviewResponse;
  job: ReviewJobSnapshot | null;
  pageData: PageData | null;
  exceptionFilter: ExceptionFilter;
  onExceptionFilterChange: (filter: ExceptionFilter) => void;
  onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
  onApplyPageFieldValue: (field: string, value: string, expectedValue?: string | null) => Promise<PageFillResult>;
  onApplyPageFieldGroupValue: (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => Promise<PageFillResult>;
  onRerun: () => Promise<void>;
}

export function ReviewResults(props: ReviewResultsProps) {
  const { review, onFocusImage } = props;
  const renderer = resolveWorkbenchRenderer(review);
  if (renderer) return renderer({ review, pageData: props.pageData, onFocusImage, onApplyPageFieldValue: props.onApplyPageFieldValue, onApplyPageFieldGroupValue: props.onApplyPageFieldGroupValue, onRerun: props.onRerun });
  return <LegacyReviewResults {...props} />;
}

function LegacyReviewResults({ review, job, pageData, exceptionFilter, onExceptionFilterChange, onFocusImage }: ReviewResultsProps) {
  const imagesById = new Map(
    (pageData?.images || []).filter((image) => image.imageId).map((image) => [image.imageId as string, image]),
  );
  const exceptionGroups = exceptionSections(review.sections, review.comparisons);
  const exceptionItems = exceptionComparisons(review.sections, review.comparisons, exceptionFilter);
  const exceptionCounts = {
    ALL: exceptionComparisons(review.sections, review.comparisons).length,
    CONFLICT: exceptionComparisons(review.sections, review.comparisons, "CONFLICT").length,
    REVIEW_REQUIRED: exceptionComparisons(review.sections, review.comparisons, "REVIEW_REQUIRED").length,
  };

  return (
    <section className="review-result">
      <ReviewTaskList tasks={review.review_tasks || []} />
      <ReviewAdvice review={review} />
      {review.business_type === "scrap_replacement" ? <QrResults review={review} /> : null}
      <section className="result-card exception-card">
        <div className="group-heading"><h2>需要处理</h2><small>{exceptionCounts.ALL} 项</small></div>
        {exceptionCounts.ALL > 0 ? (
          <div className="exception-filters" role="tablist" aria-label="异常筛选">
            {(["ALL", "CONFLICT", "REVIEW_REQUIRED"] as const).map((filter) => (
              <button
                className={exceptionFilter === filter ? "filter-button active" : "filter-button"}
                key={filter}
                type="button"
                onClick={() => onExceptionFilterChange(filter)}
              >
                {filter === "ALL" ? "全部异常" : filter === "CONFLICT" ? "冲突" : "待复核"} ({exceptionCounts[filter]})
              </button>
            ))}
          </div>
        ) : null}
        {exceptionItems.length > 0 ? (
          exceptionGroups
            .map((group) => ({
              ...group,
              comparisons: group.comparisons.filter((comparison) => exceptionFilter === "ALL" || comparison.status === exceptionFilter),
            }))
            .filter((group) => group.comparisons.length > 0)
            .map((group) => (
              <ResultGroup
                title={group.section.title}
                groupStatus={job?.groups[group.section.id]?.status}
                comparisons={group.comparisons}
                imagesById={imagesById}
                onFocusImage={onFocusImage}
                key={group.section.id}
              />
            ))
        ) : (
          <p className="field-check-empty">审核通过，字段校验未发现冲突或待复核项</p>
        )}
      </section>
      {review.sections.length === 0 && review.issues.length > 0 ? (
        <section className="result-card">
          <h2>业务处理状态</h2>
          {review.issues.map((issue) => <p className="muted" key={issue}>{issue}</p>)}
        </section>
      ) : null}
    </section>
  );
}

function QrResults({ review }: { review: ReviewResponse }) {
  return (
    <section className="result-card">
      <h2>二维码核验</h2>
      {review.qr_checks.length === 0 ? <p className="muted">暂无二维码核验结果</p> : (
        review.qr_checks.map((check, index) => {
          const presentation = qrCheckPresentation(check);
          return (
            <details className={`qr-check status-${presentation.visualStatus.toLowerCase()}`} key={`${check.image_index}-${index}`}>
              <summary className="qr-summary"><span>商务部官网</span><strong>{presentation.title}</strong></summary>
              <div className="qr-detail">
                {presentation.fields.length ? (
                  <dl className="qr-fields">
                    {presentation.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}
                  </dl>
                ) : null}
                <small>{presentation.message}</small>
              </div>
            </details>
          );
        })
      )}
    </section>
  );
}

interface ResultGroupProps {
  title: string;
  comparisons: FieldComparison[];
  groupStatus?: JobStatus;
  imagesById: Map<string, PageData["images"][number]>;
  onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
}

function ResultGroup({ title, comparisons, groupStatus, imagesById, onFocusImage }: ResultGroupProps) {
  return (
    <section className="result-card">
      <div className="group-heading"><h2>{title}</h2><small>{groupStatusLabel(groupStatus)}</small></div>
      {comparisons.length === 0 ? <p className="muted">暂无可比较字段，需接入识别工具后复核</p> : (
        comparisons.map((comparison) => {
          const sourceValues = comparisonEvidence(comparison.evidence, comparison.right_value);
          const highlightPlans = evidenceHighlightPlans(sourceValues);
          const compareByPosition = comparison.field.endsWith(".vin") || comparison.field.endsWith(".plate_no");
          return (
            <div className={`check-row status-${comparison.status.toLowerCase()}`} key={comparison.field}>
              <span>{fieldLabel(comparison.field)}</span>
              <strong>{statusLabel(comparison.status)}</strong>
              <div className="comparison-values source-values">
                {sourceValues.map((evidence, index) => (
                  <EvidenceSource
                    evidence={evidence}
                    highlightPlan={highlightPlans[index]}
                    compareByPosition={compareByPosition}
                    image={evidence.image_id ? imagesById.get(evidence.image_id) : undefined}
                    compact
                    key={`${evidence.detail || evidence.source}-${index}`}
                    onFocusImage={onFocusImage}
                  />
                ))}
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}

interface EvidenceSourceProps {
  evidence: EvidenceFact;
  highlightPlan: EvidenceHighlightPlan;
  compareByPosition?: boolean;
  image?: PageData["images"][number];
  compact?: boolean;
  onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
}

function EvidenceSource({ evidence, highlightPlan, compareByPosition = false, image, compact = false, onFocusImage }: EvidenceSourceProps) {
  const thumbnail = compact ? undefined : image?.src;
  const presentation = evidencePresentation(evidence);
  const className = `evidence-source ${presentation.kind}-evidence${evidence.conflicting ? " evidence-conflicting" : ""}${presentation.kind === "image" && thumbnail ? " has-thumbnail" : ""}`;
  const parts = highlightPlan.markAll
    ? [{ text: presentation.value, changed: Boolean(presentation.value) }]
    : (compareByPosition ? diffValueByPosition : diffValue)(presentation.value, highlightPlan.compareTo);
  const differenceTitle = highlightPlan.markAll ? "未形成多数值" : "与基准值不一致";

  return (
    <div className={className}>
      {thumbnail ? <img src={thumbnail} alt={image?.alt || "审核资料缩略图"} /> : null}
      <b className="evidence-label">{presentation.label}</b>
      <span className="evidence-value">
        {parts.map((part, index) =>
          part.changed ? <mark key={index} title={differenceTitle}>{part.text}</mark> : <span key={index}>{part.text}</span>,
        )}
      </span>
      {evidence.image_id ? (
        <button className="evidence-focus" type="button" onClick={() => void onFocusImage(evidence.image_id as string)}>查看原图</button>
      ) : null}
    </div>
  );
}

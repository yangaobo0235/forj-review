/**
 * 功能：展示最终建议和跨材料检查。
 * 职责边界：不重新计算后端建议。
 * 修改日期：2026-08-26
 * 修改人：wuyi
 */

import {
  checkStatusLabel,
  orderedCrossChecks,
  recommendationVisual,
} from "../advicePresentation";
import type { CheckResult, ReviewResponse } from "../types/review";
import { isMaterialDiagnostic } from "../reviewSteps";

function CheckValues({ check }: { check: CheckResult }) {
  if (!check.values?.length) return null;
  return (
    <dl className="advice-values">
      {check.values.map((item, index) => (
        <div key={`${item.source}-${index}`}>
          <dt>{item.source}</dt>
          <dd>{item.value == null || item.value === "" ? "未取得" : String(item.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ReviewAdvice({ review }: { review: ReviewResponse }) {
  const advice = review.agent_advice;
  const findings = (advice?.findings || []).filter(check => !isMaterialDiagnostic(check.check_id));
  const crossChecks = orderedCrossChecks(review.business_type, review.cross_checks || [])
    .filter(check => !isMaterialDiagnostic(check.check_id));
  const visual = recommendationVisual(review.recommendation);
  return (
    <section className={`recommendation recommendation-${review.recommendation.toLowerCase()}`}>
      <div className="recommendation-header">
        <span className="recommendation-icon" role="img" aria-label={visual.label}>
          {visual.symbol}
        </span>
        <div className="recommendation-copy">
          <span className="recommendation-eyebrow">Agent 最终审核建议</span>
          <strong className="recommendation-title">{advice?.title || "建议人工复核"}</strong>
          <p className="recommendation-summary">{advice?.summary || "审核建议尚未完整生成"}</p>
        </div>
      </div>

      {findings.length ? (
        <details className="advice-section">
          <summary>
            <span className="advice-section-label">需复核项目</span>
            <span className="advice-section-count">{findings.length}</span>
            <span className="advice-section-chevron" aria-hidden="true">⌄</span>
          </summary>
          <div className="advice-findings">
            {findings.map((finding) => (
              <article className="advice-finding" key={finding.check_id}>
                <div className="advice-check-heading">
                  <strong>{finding.label}</strong>
                  <b>{checkStatusLabel(finding.status)}</b>
                </div>
                <p>{finding.reason}</p>
                <CheckValues check={finding} />
              </article>
            ))}
          </div>
        </details>
      ) : null}

      {crossChecks.length ? (
        <details className="advice-section" open={crossChecks.some((check) => check.status !== "MATCH")}>
          <summary>
            <span className="advice-section-label">跨资料校验</span>
            <span className="advice-section-chevron" aria-hidden="true">⌄</span>
          </summary>
          <div className="cross-checks">
            {crossChecks.map((check) => (
              <article className={`cross-check cross-check-${check.status.toLowerCase()}`} key={check.check_id}>
                <div className="advice-check-heading">
                  <strong>{check.label}</strong>
                  <b>{checkStatusLabel(check.status)}</b>
                </div>
                <p>{check.reason}</p>
                <CheckValues check={check} />
              </article>
            ))}
          </div>
        </details>
      ) : null}

    </section>
  );
}

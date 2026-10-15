/**
 * 功能：筛选并分组需要人工关注的字段。
 * 职责边界：不把未知状态解释为异常。
 * 修改日期：2026-08-26
 * 修改人：wuyi
 */

import type { FieldComparison, ResultSection } from "../../../shared/contracts/review";

export type ExceptionFilter = "ALL" | "CONFLICT" | "REVIEW_REQUIRED";

const isException = (comparison: FieldComparison) =>
  comparison.status === "CONFLICT" || comparison.status === "REVIEW_REQUIRED";

export function exceptionSections(
  sections: ResultSection[],
  comparisons: FieldComparison[],
) {
  return sections
    .map((section) => ({
      section,
      comparisons: comparisons.filter(
        (comparison) => section.fields.includes(comparison.field) && isException(comparison),
      ),
    }))
    .filter((group) => group.comparisons.length > 0);
}

export function exceptionComparisons(
  sections: ResultSection[],
  comparisons: FieldComparison[],
  filter: ExceptionFilter = "ALL",
) {
  const allowed = filter === "ALL" ? null : filter;
  return sections.flatMap((section) =>
    comparisons
      .filter(
        (comparison) =>
          section.fields.includes(comparison.field) &&
          isException(comparison) &&
          (allowed == null || comparison.status === allowed),
      )
      .map((comparison) => ({ section, comparison })),
  );
}

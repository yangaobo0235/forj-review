import type { ReactNode } from "react";
import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type { PageFillResult } from "../../../platform/extension/page-fill-client";
import { EvidenceReviewWorkbench } from "./EvidenceReviewWorkbench";

export interface WorkbenchRendererProps {
  review: ReviewResponse;
  pageData: PageData | null;
  onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
  onApplyPageFieldValue: (field: string, value: string, expectedValue?: string | null) => Promise<PageFillResult>;
  onApplyPageFieldGroupValue: (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => Promise<PageFillResult>;
  onRerun: () => Promise<void>;
}

const renderEvidenceWorkbench = (props: WorkbenchRendererProps): ReactNode => <EvidenceReviewWorkbench {...props} />;

/** Every routed profile gets the same evidence and action UI, including generated versions. */
export function resolveWorkbenchRenderer(review: ReviewResponse): typeof renderEvidenceWorkbench | null {
  return Array.isArray(review.review_tasks) ? renderEvidenceWorkbench : null;
}

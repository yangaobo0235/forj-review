import { h, type VNode } from "vue";
import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type { PageFillResult } from "../../../platform/extension/page-action-client";
import EvidenceReviewWorkbench from "./EvidenceReviewWorkbench.vue";
import type { ModelFillResult } from "../../../shared/contracts/vehicle-model";

export interface WorkbenchRendererProps {
  review: ReviewResponse;
  pageData: PageData | null;
  onFocusImage: (imageId: string) => Promise<{ ok: boolean; error?: string }>;
  onApplyPageFieldValue: (field: string, value: string, expectedValue?: string | null) => Promise<PageFillResult>;
  onApplyPageFieldGroupValue: (fields: string[], value: string, expectedValues?: Record<string, string | null | undefined>) => Promise<PageFillResult>;
  onRerun: () => Promise<void>;
  onApplyVehicleModel?: (searchId: string, modelId: string) => Promise<ModelFillResult>;
}

const renderEvidenceWorkbench = (props: WorkbenchRendererProps): VNode => h(EvidenceReviewWorkbench, props);

/** Every routed profile gets the same evidence and action UI, including generated versions. */
export function resolveWorkbenchRenderer(review: ReviewResponse): typeof renderEvidenceWorkbench | null {
  return Array.isArray(review.review_tasks) ? renderEvidenceWorkbench : null;
}

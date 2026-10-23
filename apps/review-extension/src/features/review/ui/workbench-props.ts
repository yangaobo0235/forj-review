import type { PageData, ReviewResponse } from "../../../shared/contracts/review";
import type { PageFillResult } from "../../../shared/contracts/page-fill";
import type { ModelFillResult } from "../../../shared/contracts/vehicle-model";

export interface WorkbenchProps {
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
  onApplyVehicleModel?: (searchId: string, modelId: string) => Promise<ModelFillResult>;
}

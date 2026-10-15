export interface DifferenceRange { kind: "REPLACE" | "EXTRA" | "MISSING"; start: number; end: number; page_start: number; page_end: number; page_text: string; }
export type ImageGroup = "身份证" | "回收证明" | "旧车资料" | "新车资料" | "发票及其他图片" | "未分类";
// Identifiers come from the backend catalog; adding a page does not change this contract.
export type BusinessType = string;
export type Region = string;
export type SelectionMode = "AUTO" | "MANUAL";

export interface BusinessSelection {
  pageId?: string;
  businessType: BusinessType;
  region: Region;
  profileVersion: string;
  workflowStage: BusinessType;
  selectionMode: SelectionMode;
  /**
   * 识别置信状态。当前适配器只会给出 `CONFIRMED`（识别不到就返回 null）；
   * 保留这个字段用于将来表达"识别到了但不确定"。
   */
  detectionStatus?: "CONFIRMED";
}

/** Content Script 从审核页提取的图片元数据；不包含图片二进制。 */
export interface PageImage {
  index: number;
  imageId?: string;
  src: string;
  alt: string;
  group: ImageGroup | string;
  categoryHint?: string;
  documentTypeHint?: string;
  businessScope?: string;
  groupTitle?: string;
  groupOrder?: number;
  collectionError?: string | null;
  pagePosition?: string;
  naturalWidth?: number;
  naturalHeight?: number;
}

export interface WritableTargetSnapshot {
  field: "old_vehicle.affiliation" | "new_vehicle.affiliation";
  label: string;
  present: boolean;
  currentValue: string | null;
}

/** 页面字段 DOM 目标的跨消息快照；真实元素只保留在 Content Script 内部。 */
export interface PageFieldTargetSnapshot {
  field: string;
  present: boolean;
}

/** 当前页面实际存在的可操作表单控件，顺序和数量以本次 DOM 采集为准。 */
export interface ReviewFieldSnapshot {
  field?: string | null;
  label: string;
  value: string;
  controlType: string;
  editable: boolean;
  order: number;
  section: string;
  operationOnly?: boolean;
}

export interface PageData {
  pageId?: string;
  pageUrl: string;
  sourceTabId: number;
  pageInstanceId: string;
  pageFingerprint: string;
  collectionId: string;
  pageTitle: string;
  applicationId?: string;
  pageFields: Record<string, string>;
  reviewFields?: ReviewFieldSnapshot[];
  writableTargets?: WritableTargetSnapshot[];
  fieldTargets?: PageFieldTargetSnapshot[];
  pageText: string;
  images: PageImage[];
  businessType?: BusinessType;
  region?: Region;
  profileVersion?: string;
  workflowStage?: BusinessType;
  selectionMode?: SelectionMode;
  businessDetectionError?: string;
  /** 该次采集已被更新的采集取代；面板应提示重新采集而不是继续提交。 */
  staleCollection?: boolean;
  collectionDiagnostics?: {
    scannedControls: number;
    matchedFields: number;
    reviewFieldCount?: number;
    unmatchedLabels: string[];
    candidateCount?: number;
    ambiguousFields?: string[];
    imageSuccessCount: number;
    imageFailureCount: number;
    scannedImages?: number;
    selectedImages?: number;
    imageOverflow?: boolean;
    collectionIssues?: string[];
  };
  collectionIssues?: string[];
}

export interface MaterialCompletenessIssue {
  code: string;
  field_details?: Array<{
    field: string;
    field_label: string;
    material_name: string;
    value: unknown;
    image_id?: string | null;
    image_index?: number | null;
  }>;
  reason_code?: "material_missing" | "image_unreadable" | "recognition_failed" | "recognition_uncertain" | "evidence_not_extracted" | "page_field_missing" | null;
  reason_detail?: string | null;
  material_type?: string | null;
  business_scope?: string | null;
  field?: string | null;
  required_pages?: number[];
  present_pages?: number[];
  missing_pages?: number[];
  missing_sources?: string[];
  message: string;
  suggested_action: string;
}

export interface MaterialChecklistItem {
  key: string;
  display_name: string;
  material_type: string;
  business_scope: string;
  status: "PRESENT" | "MISSING" | "UNCERTAIN";
  required_pages: number[];
  present_pages: number[];
  missing_pages: number[];
  image_ids: string[];
  reason: string;
}

export interface MaterialCompletenessReport {
  phase: "COLLECTED" | "EXTRACTED";
  status: "COMPLETE" | "INCOMPLETE" | "UNCERTAIN";
  enforced: boolean;
  issues: MaterialCompletenessIssue[];
  checklist?: MaterialChecklistItem[];
}

export interface RetryAttempt {
  target_id: string;
  stage: "qwen" | "qr_web";
  attempt_number: number;
  reason_code: string;
  strategy: string;
  result: "succeeded" | "failed" | "skipped";
  duration_ms: number;
}

export interface RetrySummary {
  total_retries?: number;
  qwen_retries?: number;
  qr_web_retries?: number;
  attempts: RetryAttempt[];
}

export type FieldStatus = "MATCH" | "CONFLICT" | "REVIEW_REQUIRED";
export type Recommendation = "PASS" | "REVIEW_REQUIRED";
export type CheckResultStatus = "MATCH" | "CONFLICT" | "INSUFFICIENT";

export interface CheckResultValue {
  uncertain?: boolean;
  group_title?: string | null;
  differences?: DifferenceRange[];
  /** 该值与页面侧取值是否不一致；`null`/`undefined` 表示没有比对过。 */
  conflicting?: boolean | null;
  /** 产出这条值的检查写下的比对说明；无投影的字段没有这一项。 */
  check_reason?: string | null;
  source: string;
  value?: unknown;
  source_id?: string | null;
  image_id?: string | null;
  image_index?: number | null;
  document_type?: string | null;
  detail?: string | null;
  derived_from?: string | null;
  evidence_region?: number[] | null;
}

export interface CheckResult {
  check_id: string;
  label: string;
  status: CheckResultStatus;
  reason: string;
  values: CheckResultValue[];
  evidence?: EvidenceFact[];
  details?: ReviewTaskDetails;
}

export interface SubjectEvidenceRequirement {
  party: "OLD_VEHICLE" | "NEW_VEHICLE" | "SHARED";
  subject_name: string;
  subject_type: "PERSONAL" | "COMPANY";
  document: "identity_card_front" | "identity_card_back" | "business_license";
  status: "PRESENT" | "MISSING" | "UNCERTAIN";
  image_ids: string[];
  reason: string;
}

export interface ReviewTaskDetails {
  differences?: number[];
  subject_requirements?: SubjectEvidenceRequirement[];
  auxiliary_checks?: AuxiliaryCheckSummary[];
  affiliation_subject_status?: CheckResultStatus;
  /** 理由已就地表达（候选值标红、逐条说明）时不再重复整段显示。 */
  reason_distributed?: boolean;
  /** 「页面原始值」区块下面的一句话；由规则给出，前端不自己拼。 */
  page_value_note?: string;
}

export interface AuxiliaryCheckSummary {
  check_id: string;
  label: string;
  status: CheckResultStatus;
  reason: string;
}

export interface EvidenceFact {
  source: string;
  uncertain?: boolean;
  field?: string | null;
  source_id?: string | null;
  image_index?: number;
  detail?: string;
  image_id?: string | null;
  business_scope?: string | null;
  group_title?: string | null;
  group_order?: number | null;
  document_type?: string | null;
  value?: unknown;
  normalized_value?: string | null;
  derived_from?: string | null;
  evidence_region?: number[] | null;
  conflicting?: boolean;
}


export interface FieldComparison {
  field: string;
  left_value?: string | null;
  right_value?: string | null;
  status: FieldStatus;
  source: string;
  confidence?: number | null;
  evidence: EvidenceFact[];
  message: string;
  differences?: number[];
}

export interface QrCheck {
  image_index?: number | null;
  raw_value?: string | null;
  url?: string | null;
  domain_valid?: boolean | null;
  accessible?: boolean | null;
  page_fields: Record<string, string>;
  status: FieldStatus;
  message: string;
  decoder?: "model" | "zxing" | "opencv" | "none" | null;
  decode_error?: string | null;
  decode_elapsed_ms?: number | null;
}

export interface ReviewResponse {
  protocol_version: "2.0";
  presentation: "FIELD_WORKBENCH" | "MANUAL_REVIEW";
  business_type: BusinessType;
  region: Region;
  profile_version: string;
  recommendation: Recommendation;
  risk_level: string;
  summary: string;
  comparisons: FieldComparison[];
  qr_checks: QrCheck[];
  cross_checks: CheckResult[];
  issues: string[];
  sections: ResultSection[];
  context_summary?: {
    field_count?: number;
    image_count?: number;
    image_failed_count?: number;
    completed_count?: number;
    failed_count?: number;
    timed_out_count?: number;
  };
  agent_advice?: {
    decision: "PASS" | "REVIEW_REQUIRED";
    title: string;
    summary: string;
    confidence?: number | null;
    recognition_confidence?: number | null;
    findings: CheckResult[];
    basis: string[];
    limitations: string[];
  } | null;
  material_completeness?: MaterialCompletenessReport | null;
  retry_summary?: RetrySummary | null;
  capability_plan?: CapabilityPlanEntry[];
  capability_results?: CapabilityResult[];
  page_fill_intent?: PageFillAction[];
  page_actions?: PageActionIntent[];
  review_tasks?: ReviewTask[];
}

export interface PageActionIntent {
  action_id: string;
  payload: Record<string, unknown>;
  requires_authorization: boolean;
}

export interface CapabilityPlanEntry {
  capability_id: string;
  status: "READY" | "SKIPPED" | "BLOCKED" | "NOT_CONFIGURED";
  stage: string;
  reason: string;
  dependencies: string[];
  missing_dependencies: string[];
}

export interface CapabilityResult {
  capability_id: string;
  status: "READY" | "SKIPPED" | "BLOCKED" | "NOT_CONFIGURED" | "SUCCEEDED" | "FAILED";
  checks?: CheckResult[];
  evidence?: EvidenceFact[];
  qr_checks?: QrCheck[];
  display_items?: string[];
  limitations?: string[];
  error_code?: string | null;
  attempts?: number;
}

export type ReviewDisplayTarget = "PAGE_FIELD" | "ASSISTANT";

export interface ReviewTask {
  step_id: string;
  /** Business visibility is projected by the backend; it does not change machine status. */
  visible?: boolean;
  sequence: number;
  /** MATERIAL is accepted only for old responses and excluded from display. */
  category: "FIELD" | "EXTERNAL" | "BUSINESS_RULE" | "MATERIAL";
  display_target: ReviewDisplayTarget;
  page_field?: string | null;
  /** Canonical DOM field for sidebar tasks; distinct from PAGE_FIELD rendering. */
  page_target_field?: string | null;
  /** All DOM fields written together when this is a composite field task. */
  page_target_fields?: string[];
  page_value?: unknown;
  /** Raw values captured from each page field in a composite task. */
  page_values?: CheckResultValue[];
  control_type?: string | null;
  writable?: boolean;
  requires_reviewer_action: boolean;
  label: string;
  result_status: CheckResultStatus;
  reason: string;
  values: CheckResultValue[];
  evidence: EvidenceFact[];
  details?: ReviewTaskDetails;
  evidence_count?: number;
  evidence_mode?: string | null;
  evidence_sources?: string[];
  not_found_sources?: string[];
  normalized_page_value?: string | null;
}

export interface PageFillAction {
  field: "old_vehicle.affiliation" | "new_vehicle.affiliation";
  target_label: string;
  owner_type: "PERSONAL" | "COMPANY";
}

/** 审核员从助手中选择候选值后发起的单字段页面回填。 */
export interface PageWriteAction {
  field: string;
  value: string;
  expectedValue?: string | null;
}

export interface PageWriteGroupAction {
  fields: string[];
  value: string;
  expectedValues: Record<string, string | null | undefined>;
}

export interface ResultSection {
  id: string;
  title: string;
  fields: string[];
}

export type JobStatus = "RUNNING" | "PARTIAL" | "COMPLETED" | "FAILED" | "CANCELLED";
export type ReviewJobPhase = "PREPARING" | "RECOGNIZING" | "FINALIZING" | "COMPLETED" | "FAILED" | "CANCELLED";

export interface ReviewProgress {
  total_count: number;
  submitted_count: number;
  completed_count: number;
  failed_count: number;
  timed_out_count: number;
}

export interface ReviewGroupProgress extends ReviewProgress {
  status: JobStatus;
}

export interface ReviewJobCreated {
  job_id: string;
  status: JobStatus;
  created_at: string;
}

export interface ReviewJobSnapshot {
  job_id: string;
  status: JobStatus;
  created_at: string;
  progress: ReviewProgress;
  failed_image_ids?: string[];
  timed_out_image_ids?: string[];
  groups: Record<string, ReviewGroupProgress>;
  result?: ReviewResponse | null;
  message?: string | null;
  material_completeness?: MaterialCompletenessReport | null;
  retry_summary?: RetrySummary | null;
  phase: ReviewJobPhase;
}

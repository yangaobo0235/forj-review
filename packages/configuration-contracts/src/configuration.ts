import type { CollectManifest } from "./collection.ts";

export interface ConfigurationSnapshot {
  schema_version: "2.0";
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  title: string;
  detected_page_id?: string;
  configuration_version?: string;
  record_identity?: Record<string, string>;
  structure_digest: string;
  content_digest?: string;
  headings?: string[];
  fields: Array<{
    candidate_id: string;
    label: string;
    section: string;
    control_kind: string;
    editable: boolean;
    value: string | null;
    locator_id: string;
    locator_name: string;
    group_label?: string;
  }>;
  samples: Array<{
    sample_id: string;
    url: string;
    label: string;
    group: string;
    source_kind?: "http" | "unsupported";
    section?: string;
    group_path?: string[];
    locator?: Record<string, string>;
  }>;
  diagnostics: string[];
}

export interface ConfigurationPending {
  id: string;
  kind: "question" | "browser" | "confirmation";
  confirmed?: boolean;
  manual?: boolean;
  question?: string;
  options?: string[];
  manifest?: CollectManifest;
  page_catalog?: {
    version: string;
    identities: Array<{
      business_type: string;
      region: string;
      profile_version: string;
      page_id?: string;
      title: string;
      paths: string[];
      anchors: string[];
      review_tab?: string;
    }>;
  };
  page_id?: string;
  snapshot_id?: string;
  page_instance_id?: string;
  page_url?: string;
  structure_digest?: string;
  candidate_digest?: string;
}

export interface ConfigurationTask {
  task_id: string;
  revision: number;
  row_version?: number;
  evidence_revision?: number;
  baseline_version?: string;
  intent_revision?: number;
  route?: "UNDECIDED" | "CONFIG" | "LOCAL_CODE" | "HUMAN_HANDOFF";
  waiting_reason?: string | null;
  route_assessment?: { reason: string } | null;
  activation_only?: boolean;
  environment?: string;
  execution_mode?: "CONFIGURATION_SERVICE";
  comparison_configurable_fields?: string[];
  parent_task_id?: string | null;
  child_tasks?: Array<{ task_id: string; kind: string; reason: string }>;
  local_report?: {
    status: string;
    changed_files?: string[];
    patch_digest?: string;
    verification_scope?: string;
    notice?: string;
    delivery?: { summary: string; obstacles: string[] };
    evidence?: Array<{ id: string; tool: string; result: Record<string, unknown> }>;
  } | null;
  task: {
    stage: string;
    progress_step: number;
    label: string;
    complete: boolean;
    can_publish: boolean;
    local_report_current?: boolean;
    coverage: Array<{
      requirement_id: string;
      text: string;
      resolved: boolean;
      verified: boolean;
      acceptance?: string[];
    }>;
  };
  preview?: { diff: unknown; candidate_digest: string } | null;
  activation?: { state: string; reason?: string; checked_at?: string } | null;
  adoption_task?: AdoptionTask | null;
  adoption?: { version: string; checked_at: string } | null;
  field_dependencies?: Record<string, string[]>;
  tool_results?: Record<
    string,
    {
      kind?: string;
      ok?: boolean;
      revision?: number;
      case_set_id?: string;
      binding?: { revision: number };
      expires_at?: string;
    }
  >;
  state: string;
  turn_status: string;
  snapshot: ConfigurationSnapshot | null;
  capture_id: string | null;
  reference_fields: string[];
  requirements?: Array<{
    requirement_id: string;
    text: string;
    status?: string;
    acceptance?: string[];
  }>;
  reference_samples: string[];
  decisions: Array<{
    status: string;
    revision: number;
    reason: string;
    field_key?: string;
    material?: string;
    candidate_id?: string;
    sample_id?: string;
  }>;
  capability_gaps?: Array<{ missing_capability: string; next_step: string }>;
  draft: Record<string, unknown>;
  page_options?: Record<string, unknown>;
  pending: ConfigurationPending | null;
  messages: Array<{ role: string; content: string }>;
  chat_messages: Array<{ role: "assistant" | "user"; content: string }>;
  report: {
    ok: boolean;
    report_id: string;
    candidate_digest: string;
    blockers: string[];
    diff: unknown;
    decision_summary?: string[];
    checks?: Array<{ name: string; ok: boolean }>;
  } | null;
  receipt: Record<string, unknown> | null;
  error: string | null;
  execution_summary?: ExecutionActivity | null;
}

export interface CapturedPage {
  capture_id: string;
  extension_id: string;
  snapshot: ConfigurationSnapshot;
  created_at: string;
}
export interface CaptureSummary {
  capture_id: string;
  title: string;
  created_at: string;
  field_count: number;
  sample_count: number;
  page_id: string;
  page_url: string;
  diagnostics: string[];
}
export interface TaskSummary {
  task_id: string;
  state: string;
  title: string;
  updated_at: string;
  label?: string;
  summary?: string;
  page_id?: string;
}
export interface PageTemplate {
  page_id: string;
  title: string;
  region: string;
  versions: string[];
}
export interface ExecutionActivity {
  title: string;
  detail: string;
  next_action?: string;
  findings?: string[];
}
export interface ConfigurationEvent {
  seq: number;
  run_id?: string | null;
  created_at?: string;
  type: string;
  data: Record<string, unknown> & { activity?: ExecutionActivity };
}
export interface AdoptionTask {
  id: string;
  kind: "adoption";
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  page_id: string;
  version: string;
  effective_digest: string;
  expires_at: string;
}
export interface AdoptionResult {
  task_id: string;
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  page_id: string;
  version: string;
  effective_digest: string;
  fields: Record<string, number>;
  diagnostics: string[];
}
export type BrowserBridgeRequest =
  | {
      type: "CONFIGURATION_COLLECTION_TRIAL";
      capture_id: string;
      task: ConfigurationPending;
    }
  | { type: "CONFIGURATION_ADOPTION_CHECK"; capture_id: string; task: AdoptionTask };
export interface CollectionTrialResult {
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  structure_digest: string;
  candidate_digest: string;
  fields: Record<string, number>;
  control_kinds: Record<string, string>;
  samples: Record<string, string>;
  matched_page_id: string;
  diagnostics: string[];
}

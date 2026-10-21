import type { CollectManifest } from "./collection.ts";

export interface ConfigurationSnapshot {
  schema_version: "1.0";
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  title: string;
  structure_digest: string;
  headings?: string[];
  fields: Array<{ candidate_id: string; label: string; section: string; control_kind: string;
    editable: boolean; value: string | null; locator_id: string; locator_name: string; group_label?: string }>;
  samples: Array<{ sample_id: string; url: string; label: string; group: string }>;
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
  page_catalog?: {version: string; identities: Array<{business_type: string; region: string;
    profile_version: string; page_id?: string; title: string; paths: string[]; anchors: string[]; review_tab?: string}>};
  page_id?: string;
  snapshot_id?: string;
  page_instance_id?: string;
  page_url?: string;
  structure_digest?: string;
  candidate_digest?: string;
}

export interface ConfigurationSession {
  session_id: string;
  revision: number;
  activation_only?: boolean;
  field_dependencies?: Record<string, string[]>;
  tool_results?: Record<string, {kind?: string; ok?: boolean; revision?: number; case_set_id?: string; binding?: {revision: number}; expires_at?: string}>;
  state: string;
  turn_status: string;
  snapshot: ConfigurationSnapshot;
  capture_id: string | null;
  selected_fields: string[];
  requirements?: Array<{requirement_id: string; text: string}>;
  selected_samples: string[];
  required_samples: string[];
  decisions: Array<{status: string; revision: number; reason: string; field_key?: string; material?: string; candidate_id?: string; sample_id?: string}>;
  draft: Record<string, unknown>;
  page_options?: Record<string, unknown>;
  pending: ConfigurationPending | null;
  messages: Array<{role: string; content: string}>;
  chat_messages?: Array<{role: "assistant" | "user"; content: string}>;
  report: {ok: boolean; report_id: string; candidate_digest: string; blockers: string[]; diff: unknown; decision_summary?: string[]} | null;
  receipt: Record<string, unknown> | null;
  error: string | null;
}

export interface CapturedPage {
  capture_id: string;
  extension_id: string;
  snapshot: ConfigurationSnapshot;
  created_at: string;
}
export interface CaptureSummary {
  capture_id: string; title: string; created_at: string; field_count: number; sample_count: number;
}
export interface SessionSummary {session_id: string; state: string; title: string; updated_at: string}
export interface PageTemplate {page_id: string; title: string; region: string; versions: string[]}
export interface ConfigurationEvent {seq: number; type: string; data: Record<string, unknown>}
export interface BrowserBridgeRequest {
  type: "CONFIGURATION_COLLECTION_TRIAL";
  capture_id: string;
  task: ConfigurationPending;
}
export interface CollectionTrialResult {
  snapshot_id: string; page_instance_id: string; page_url: string; structure_digest: string;
  candidate_digest: string; fields: Record<string, number>; control_kinds: Record<string, string>;
  samples: Record<string, string>; matched_page_id: string; diagnostics: string[];
}

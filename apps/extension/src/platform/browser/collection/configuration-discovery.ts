import { ReviewPageFieldCollector } from "./page-field-collector.ts";
import { formControlKind } from "../form-controls.ts";
import { applyCollectManifest, currentCollectManifest } from "../catalog/collect-manifest.ts";
import { matchPageIdentity, reviewTabLabels } from "../catalog/page-catalog.ts";
import { readActiveReviewTab } from "../identity/active-review-tab.ts";
import { collectImageCandidates } from "./collect-image-candidates.ts";
import { ReviewImageCandidates } from "./image-candidates.ts";
import { classifyImage, imageDocumentType } from "./material-hints.ts";
import { imageLabelFor } from "./image-label.ts";
import { createPageInstanceId } from "../identity/page-identity.ts";
import type { ConfigurationPending, ConfigurationSnapshot } from "../../../shared/contracts/configuration.ts";

let pageInstanceId = "";
let lastSnapshot: ConfigurationSnapshot | null = null;

async function digest(value: unknown): Promise<string> {
  const text = JSON.stringify(value);
  if (globalThis.crypto?.subtle) {
    const data = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(data), byte => byte.toString(16).padStart(2, "0")).join("");
  }
  // HTTP admin pages lack SubtleCrypto. This fingerprint detects drift; it is not authentication.
  let left = 2166136261, right = 5381;
  for (let index = 0; index < text.length; index++) {
    left = Math.imul(left ^ text.charCodeAt(index), 16777619);
    right = Math.imul(right, 33) ^ text.charCodeAt(index);
  }
  return `structure-v1:${text.length}:${left >>> 0}:${right >>> 0}`;
}

export async function discoverConfigurationPage(): Promise<ConfigurationSnapshot> {
  pageInstanceId ||= createPageInstanceId();
  const candidates = ReviewPageFieldCollector.discover(document);
  const seen = new Set<string>();
  const elements = new Map<unknown, number>();
  const fields: ConfigurationSnapshot["fields"] = [];
  for (const candidate of candidates) {
    const element = candidate.element;
    if (element && !elements.has(element)) elements.set(element, elements.size);
    const key = JSON.stringify([element ? elements.get(element) : null, candidate.label, candidate.section, candidate.value]);
    if (seen.has(key)) continue;
    seen.add(key);
    fields.push({candidate_id: `field_${fields.length}`, label: candidate.label, section: candidate.section || "unknown",
      group_label: element?.closest?.("fieldset,section,.ant-card,.ant-collapse-item")?.querySelector?.("legend,h2,h3,h4,.ant-card-head-title")?.textContent?.trim().slice(0, 100) || "",
      control_kind: element ? formControlKind(element) : "readonly",
      editable: Boolean(element && ReviewPageFieldCollector.isEditableControl(element)),
      value: String(candidate.value ?? ""), locator_id: element?.getAttribute?.("id") || "",
      locator_name: element?.getAttribute?.("name") || ""});
  }
  const visibleImages = [...document.images].filter(image => image.getClientRects().length);
  const images = visibleImages.filter(image => /^https?:\/\//i.test(image.currentSrc || image.src));
  const samples = images.slice(0, 32).map((image, index) => ({sample_id: `sample_${index}`,
    url: image.currentSrc || image.src, label: imageLabelFor(image) || image.alt || image.closest("figure")?.textContent?.trim().slice(0, 100) || "",
    group: image.closest("section,fieldset,.ant-form-item")?.textContent?.trim().slice(0, 120) || ""}));
  return {schema_version: "1.0", snapshot_id: createPageInstanceId(), page_instance_id: pageInstanceId,
    page_url: location.href, title: document.title,
    headings: [...new Set([...document.querySelectorAll("h1,h2,h3,h4,legend,.ant-card-head-title,.ant-descriptions-title,.ant-collapse-header")]
      .map(element => element.textContent?.trim().slice(0, 100) || "").filter(Boolean))].slice(0, 100),
    structure_digest: await digest({url: location.href, fields, samples}), fields: fields.slice(0, 500), samples,
    diagnostics: [...(fields.length > 500 ? ["字段候选超过 500 个，请缩小页面范围"] : []),
      ...(images.length > 32 ? ["图片超过 32 张，请缩小页面范围"] : []),
      ...(visibleImages.length > images.length ? ["存在非 HTTP(S) 图片，本版不能分析这些图片，请检查是否遗漏材料"] : [])]};
}

export async function captureConfigurationPage() {
  lastSnapshot = await discoverConfigurationPage();
  return lastSnapshot;
}

export async function trialConfigurationPage(task: ConfigurationPending) {
  if (!lastSnapshot || lastSnapshot.snapshot_id !== task.snapshot_id) throw new Error("原始采集已失效，请重新建立页面会话");
  const current = await discoverConfigurationPage();
  if (current.page_url !== task.page_url || current.page_instance_id !== task.page_instance_id
      || current.structure_digest !== task.structure_digest) throw new Error("页面记录或结构已变化，请重新采集");
  const previous = currentCollectManifest();
  try {
    if (!task.manifest || !applyCollectManifest(task.manifest)) throw new Error("候选采集清单无效");
    const reviewTab = readActiveReviewTab(document, reviewTabLabels(task.page_catalog ?? null, location.href));
    const collected = ReviewPageFieldCollector.collect(reviewTab.panel ?? document);
    const fields = Object.fromEntries(task.manifest.fields.map(field => [field.key,
      collected.ambiguousFields.includes(field.key) ? 2 : Object.hasOwn(collected.pageFields, field.key) ? 1 : 0]));
    const controlKinds = Object.fromEntries(collected.fieldTargets.map(target => [target.field, formControlKind(target.element)]));
    const images = ReviewImageCandidates.select(collectImageCandidates(classifyImage), 16);
    const samples: Record<string, string> = {};
    for (const sample of lastSnapshot.samples) {
      const matched = images.selected.filter(image => image.src === sample.url);
      const types = new Set(matched.map(image => imageDocumentType(image)));
      if (types.size === 1 && !types.has("unknown")) samples[sample.sample_id] = [...types][0];
    }
    const identity = matchPageIdentity(task.page_catalog ?? null, location.href, document.body.innerText || "", reviewTab);
    return {snapshot_id: task.snapshot_id, page_instance_id: current.page_instance_id, page_url: current.page_url,
      structure_digest: current.structure_digest, candidate_digest: task.candidate_digest,
      fields, control_kinds: controlKinds, samples, matched_page_id: identity?.pageId ?? "",
      diagnostics: [...current.diagnostics, ...(images.overflow ? ["正式采集材料超过 16 张，请缩小配置范围"] : [])]};
  } finally {
    applyCollectManifest(previous);
  }
}

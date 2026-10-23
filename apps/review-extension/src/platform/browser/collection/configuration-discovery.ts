import { ReviewPageFieldCollector } from "./page-field-collector.ts";
import { formControlKind } from "../form-controls.ts";
import { applyCollectManifest, currentCollectManifest } from "../catalog/collect-manifest.ts";
import { matchPageIdentity, reviewTabLabels } from "../catalog/page-catalog.ts";
import type { PageCatalog } from "../catalog/page-catalog.ts";
import { readActiveReviewTab } from "../identity/active-review-tab.ts";
import { collectImageCandidates } from "./collect-image-candidates.ts";
import { ReviewImageCandidates } from "./image-candidates.ts";
import { classifyImage, imageDocumentType } from "./material-hints.ts";
import { imageLabelFor } from "./image-label.ts";
import { createPageInstanceId } from "../identity/page-identity.ts";
import { manifestRecordIdentityFields } from "../catalog/collect-manifest.ts";
import type {
  ConfigurationPending,
  ConfigurationSnapshot,
} from "../../../shared/contracts/configuration.ts";

let pageInstanceId = "";
let lastSnapshot: ConfigurationSnapshot | null = null;
let discoveryRoot: HTMLElement | null = null;
let imageSources = new Map<string, string>();
let discoveredImageSources = new Map<string, string>();

async function digest(value: unknown): Promise<string> {
  const text = JSON.stringify(value);
  if (globalThis.crypto?.subtle) {
    const data = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(data), (byte) => byte.toString(16).padStart(2, "0")).join("");
  }
  // HTTP admin pages lack SubtleCrypto. This fingerprint detects drift; it is not authentication.
  let left = 2166136261,
    right = 5381;
  for (let index = 0; index < text.length; index++) {
    left = Math.imul(left ^ text.charCodeAt(index), 16777619);
    right = Math.imul(right, 33) ^ text.charCodeAt(index);
  }
  return `structure-v1:${text.length}:${left >>> 0}:${right >>> 0}`;
}

export async function discoverConfigurationPage(): Promise<ConfigurationSnapshot> {
  pageInstanceId ||= createPageInstanceId();
  if (discoveryRoot && !discoveryRoot.isConnected) throw new Error("原审核区域已变化，请重新采集");
  const root = discoveryRoot ?? document;
  const candidates = ReviewPageFieldCollector.discover(root);
  const seen = new Set<string>();
  const elements = new Map<unknown, number>();
  const fields: ConfigurationSnapshot["fields"] = [];
  for (const candidate of candidates) {
    const element = candidate.element;
    if (element && !elements.has(element)) elements.set(element, elements.size);
    const key = JSON.stringify([
      element ? elements.get(element) : null,
      candidate.label,
      candidate.section,
      candidate.value,
    ]);
    if (seen.has(key)) continue;
    seen.add(key);
    fields.push({
      candidate_id: `field_${fields.length}`,
      label: candidate.label,
      section: candidate.section || "unknown",
      group_label:
        element
          ?.closest?.("fieldset,section,.ant-card,.ant-collapse-item")
          ?.querySelector?.("legend,h2,h3,h4,.ant-card-head-title")
          ?.textContent?.trim()
          .slice(0, 100) || "",
      control_kind: element ? formControlKind(element) : "readonly",
      editable: Boolean(element && ReviewPageFieldCollector.isEditableControl(element)),
      value: String(candidate.value ?? ""),
      locator_id: element?.getAttribute?.("id") || "",
      locator_name: element?.getAttribute?.("name") || "",
    });
  }
  const visibleImages = [...document.images].filter(
    (image) => root.contains(image) && image.getClientRects().length,
  );
  const images = visibleImages.filter(
    (image) =>
      /^(?:https?:|blob:|data:image\/)/i.test(image.currentSrc || image.src) &&
      !/(?:^|[-_\s])(logo|icon|avatar|favicon|badge|spinner)(?:$|[-_\s])/i.test(
        `${image.className} ${image.alt}`,
      ) &&
      image.getAttribute("role") !== "presentation" &&
      image.getAttribute("aria-hidden") !== "true",
  );
  const samples = images.slice(0, 32).map((image, index) => {
    const candidates = [
      image.currentSrc,
      image.src,
      image.getAttribute("data-src"),
      image.closest("a")?.href,
    ];
    const source = candidates.find((value) => value && /^https?:/i.test(value)) || "";
    const group = image.closest("section,fieldset,.ant-form-item,figure,.ant-card");
    const heading =
      group
        ?.querySelector("legend,h2,h3,h4,.ant-form-item-label,.ant-card-head-title")
        ?.textContent?.trim()
        .slice(0, 120) || "";
    return {
      sample_id: `sample_${index}`,
      url: source,
      source_kind: source ? ("http" as const) : ("unsupported" as const),
      label:
        imageLabelFor(image) ||
        image.alt ||
        image.closest("figure")?.querySelector("figcaption")?.textContent?.trim().slice(0, 100) ||
        "",
      group: heading,
      group_path: heading ? [heading] : [],
      section: group?.getAttribute("aria-label") || heading,
      locator: { id: image.id, slot: image.getAttribute("data-field") || "" },
    };
  });
  discoveredImageSources = new Map(
    samples.map((sample, index) => [
      sample.sample_id,
      images[index]!.currentSrc || images[index]!.src,
    ]),
  );
  const hiddenMaterials = ReviewImageCandidates.select(
    collectImageCandidates(classifyImage)
      .filter(
        (candidate) =>
          root.contains(candidate.image) &&
          !candidate.visible &&
          !images.some((image) => (image.currentSrc || image.src) === candidate.src),
      )
      .map((candidate) => ({ ...candidate, visible: true })),
    32,
  ).eligibleCount;
  return {
    schema_version: "2.0",
    snapshot_id: createPageInstanceId(),
    page_instance_id: pageInstanceId,
    page_url: location.href,
    title: document.title,
    headings: [
      ...new Set(
        [
          ...root.querySelectorAll(
            "h1,h2,h3,h4,legend,.ant-card-head-title,.ant-descriptions-title,.ant-collapse-header",
          ),
        ]
          .map((element) => element.textContent?.trim().slice(0, 100) || "")
          .filter(Boolean),
      ),
    ].slice(0, 100),
    structure_digest: await digest({
      fields: fields.map((field) => ({
        candidate_id: field.candidate_id,
        label: field.label,
        section: field.section,
        group_label: field.group_label,
        control_kind: field.control_kind,
        editable: field.editable,
        locator_id: field.locator_id,
        locator_name: field.locator_name,
      })),
      samples: samples.map((sample) => ({
        sample_id: sample.sample_id,
        label: sample.label,
        group: sample.group,
        group_path: sample.group_path,
        section: sample.section,
        locator: sample.locator,
      })),
    }),
    content_digest: await digest({ url: location.href, fields, samples }),
    fields: fields.slice(0, 500),
    samples,
    diagnostics: [
      ...(samples.some((sample) => sample.source_kind === "unsupported")
        ? ["部分图片没有 HTTP(S) 原始地址，相关材料分析时需要补充来源"]
        : []),
      ...(fields.length > 500 ? ["字段候选超过 500 个，请缩小页面范围"] : []),
      ...(images.length > 32 ? ["图片超过 32 张，请缩小页面范围"] : []),
      ...(hiddenMaterials ? ["存在隐藏的材料图片，请展开相关审核区域后重新采集"] : []),
      ...visibleImages
        .filter((image) => !/^(?:https?:|blob:|data:image\/)/i.test(image.currentSrc || image.src))
        .map(() => "存在无法读取的图片来源，请检查材料"),
      ...[...root.querySelectorAll<HTMLImageElement>("img[data-src]")]
        .filter(
          (image) =>
            !image.src || (image.dataset.src && image.dataset.src !== image.getAttribute("src")),
        )
        .map(() => "存在尚未加载的图片，请展开材料并完成加载后重新采集"),
    ],
  };
}

export async function captureConfigurationPage(catalog: PageCatalog | null = null) {
  const tab = readActiveReviewTab(document, reviewTabLabels(catalog, location.href));
  discoveryRoot = tab.panel;
  lastSnapshot = await discoverConfigurationPage();
  imageSources = new Map(discoveredImageSources);
  const identity = matchPageIdentity(catalog, location.href, document.body.innerText || "", tab);
  lastSnapshot.detected_page_id = identity?.pageId ?? "";
  lastSnapshot.configuration_version = identity?.profileVersion ?? "";
  const current = currentCollectManifest() as {
    business_type?: string;
    profile_version?: string;
  } | null;
  if (
    identity &&
    current?.business_type === identity.businessType &&
    current.profile_version === identity.profileVersion
  ) {
    lastSnapshot.record_identity = ReviewPageFieldCollector.collectIdentity(
      tab.panel ?? document,
      manifestRecordIdentityFields() ?? [],
    ).pageFields;
  }
  return lastSnapshot;
}

export async function trialConfigurationPage(task: ConfigurationPending) {
  if (!lastSnapshot || lastSnapshot.snapshot_id !== task.snapshot_id)
    throw new Error("原始采集已失效，请重新建立页面会话");
  const current = await discoverConfigurationPage();
  if (
    current.page_url !== task.page_url ||
    current.page_instance_id !== task.page_instance_id ||
    current.structure_digest !== task.structure_digest
  )
    throw new Error("页面记录或结构已变化，请重新采集");
  const previous = currentCollectManifest();
  // Identity has its own check. Structural stability alone cannot distinguish two
  // records displayed in the same SPA view; unrelated values/URLs are not structure.
  const savedIdentity = lastSnapshot.record_identity ?? {};
  if (Object.keys(savedIdentity).length) {
    const identity = ReviewPageFieldCollector.collectIdentity(
      discoveryRoot ?? document,
      manifestRecordIdentityFields() ?? [],
    ).pageFields;
    if (JSON.stringify(identity) !== JSON.stringify(savedIdentity))
      throw new Error("页面记录身份已变化，请重新采集");
  } else if (
    (await digest(current.fields.map((f) => [f.candidate_id, f.value]))) !==
    (await digest(lastSnapshot.fields.map((f) => [f.candidate_id, f.value])))
  ) {
    throw new Error("页面记录或结构已变化；未确认独立记录身份，请重新采集");
  }
  try {
    if (!task.manifest || !applyCollectManifest(task.manifest)) throw new Error("候选采集清单无效");
    const reviewTab = readActiveReviewTab(
      document,
      reviewTabLabels(task.page_catalog ?? null, location.href),
    );
    const collected = ReviewPageFieldCollector.collect(reviewTab.panel ?? document);
    const fields = Object.fromEntries(
      task.manifest.fields.map((field) => [
        field.key,
        collected.ambiguousFields.includes(field.key)
          ? 2
          : Object.hasOwn(collected.pageFields, field.key)
            ? 1
            : 0,
      ]),
    );
    const controlKinds = Object.fromEntries(
      collected.fieldTargets.map((target) => [target.field, formControlKind(target.element)]),
    );
    const images = ReviewImageCandidates.select(collectImageCandidates(classifyImage), 16);
    const samples: Record<string, string> = {};
    for (const sample of lastSnapshot.samples) {
      const matched = images.selected.filter(
        (image) => image.src === imageSources.get(sample.sample_id),
      );
      const types = new Set(matched.map((image) => imageDocumentType(image)));
      if (types.size === 1 && !types.has("unknown")) samples[sample.sample_id] = [...types][0];
    }
    const identity = matchPageIdentity(
      task.page_catalog ?? null,
      location.href,
      document.body.innerText || "",
      reviewTab,
    );
    return {
      snapshot_id: task.snapshot_id,
      page_instance_id: current.page_instance_id,
      page_url: current.page_url,
      structure_digest: current.structure_digest,
      candidate_digest: task.candidate_digest,
      fields,
      control_kinds: controlKinds,
      samples,
      matched_page_id: identity?.pageId ?? "",
      diagnostics: [
        ...current.diagnostics,
        ...(images.overflow ? ["正式采集材料超过 16 张，请缩小配置范围"] : []),
      ],
    };
  } finally {
    applyCollectManifest(previous);
  }
}

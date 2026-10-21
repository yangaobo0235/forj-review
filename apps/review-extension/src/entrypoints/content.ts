import { imageAssetMetadata } from "../platform/browser/collection/image-assets.ts";
import { collectImageCandidates } from "../platform/browser/collection/collect-image-candidates.ts";
import { applyCollectManifest, manifestIdentityAnchors, manifestRecordIdentityFields, manifestWritableControlKinds, manifestWritableFields } from "../platform/browser/catalog/collect-manifest.ts";
import type { CollectManifest } from "../platform/browser/catalog/collect-manifest.ts";
import { appliedPageCatalog, effectivePageCatalog, applyPageCatalog, explainPageIdentity, reviewTabLabels } from "../platform/browser/catalog/page-catalog.ts";
import type { PageCatalog } from "../platform/browser/catalog/page-catalog.ts";
import { ReviewBusinessDetector } from "../platform/browser/identity/business-detector.ts";
import { guardActiveReviewTab, readActiveReviewTab, reviewTabCollectionRoot } from "../platform/browser/identity/active-review-tab.ts";
import { ReviewImageCandidates } from "../platform/browser/collection/image-candidates.ts";
import { ReviewImageFocus } from "../platform/browser/actions/image-focus.ts";
import { classifyImage } from "../platform/browser/collection/material-hints.ts";
import { ReviewPageFieldCollector } from "../platform/browser/collection/page-field-collector.ts";
import { ReviewPageFieldWriter } from "../platform/browser/actions/page-field-writer.ts";
import { buildPageFingerprint, createPageInstanceId, sameRecordExceptFields } from "../platform/browser/identity/page-identity.ts";
import type { BusinessSelection, PageWriteGroupAction } from "../shared/contracts/review.ts";
import { logDiagnostic } from "../shared/diagnostics.ts";
import { captureConfigurationPage, trialConfigurationPage } from "../platform/browser/collection/configuration-discovery";
import type { ConfigurationPending } from "../shared/contracts/configuration";
import { createVehicleModelFiller } from "../platform/browser/actions/vehicle-model-fill.ts";
import { manifestActions, manifestFieldLabels } from "../platform/browser/catalog/collect-manifest.ts";
/**
 * 功能：协调页面字段、图片地址采集和原图定位消息。
 * 职责边界：复杂识别逻辑委托给独立公共脚本；DOM 元素不跨消息传输。
 * 修改日期：2026-09-11
 */

// Keep collection orchestration here; field, image, and focus rules live in dedicated scripts.
const reviewImageElements = new Map();
const reviewFieldElements = new Map();
const verifiedFieldWrites = new Map<string, { original: unknown; value: string }>();
// 同一采集批次内，连续回填只更新最新值，身份基准始终保留第一次回填前的值。
const rememberVerifiedWrite = (field: string, original: unknown, value: string) => {
  const previous = verifiedFieldWrites.get(field);
  verifiedFieldWrites.set(field, { original: previous ? previous.original : original, value });
};
const MAX_REVIEW_IMAGES = 16;
const pageInstanceId = createPageInstanceId();
let activeCollectionId = "";
let latestCollectionId = "";
let collectionSequence = 0;
let reviewTabGuard: ReturnType<typeof guardActiveReviewTab> | null = null;
let activeReviewRoot: HTMLElement | null = null;
let activeCollectionBusiness = "";
const sameReviewStep = () => reviewTabGuard?.isCurrent() ?? true;
const nextCollectionId = () => `${pageInstanceId}:${++collectionSequence}`;
// 指纹锚点来自本次业务的采集清单；没有清单时不启用身份判定。
const pageFingerprint = (fields: Record<string, unknown>) =>
  buildPageFingerprint(fields, manifestIdentityAnchors() ?? undefined, manifestRecordIdentityFields() ?? undefined);
const MESSAGE_TYPES = Object.freeze({
  collectPageData: "COLLECT_PAGE_DATA",
  collectPageManifest: "COLLECT_PAGE_MANIFEST",
  focusReviewImage: "FOCUS_REVIEW_IMAGE",
  applyPageFieldValue: "APPLY_PAGE_FIELD_VALUE",
  applyPageFieldGroupValue: "APPLY_PAGE_FIELD_GROUP_VALUE",
  verifyInvoice: "VERIFY_INVOICE",
  setAffiliation: "SET_AFFILIATION",
});
type ReviewMessage = {
  type?: string; expectedPageUrl?: string; expectedPageInstanceId?: string; expectedPageFingerprint?: string; expectedCollectionId?: string;
  action?: { field?: string; value?: string; expectedValue?: string | null }; actions?: unknown[]; imageId?: string; businessSelection?: unknown;
  [key: string]: unknown;
};
type CollectionSnapshot = { pageFields?: Record<string, string>; fieldTargets?: Array<{ field: string; element: Element }> };
// 回填权限只来自本次采集清单。
/** 本次清单允许写回的字段；没有清单时禁止回填。 */
const writableFields = () => manifestWritableFields() ?? new Set<string>();

/** 本次清单允许写回的控件类型；没有清单时不启用写回。 */
const writableControlKinds = () => manifestWritableControlKinds();

const controlFields = (collection: CollectionSnapshot) => {
  const fields = { ...collection.pageFields };
  for (const { field, element } of collection.fieldTargets || []) {
    const snapshot = ReviewPageFieldWriter?.captureValue?.(element as HTMLElement);
    if (snapshot) fields[field] = snapshot.value;
  }
  return fields;
};

const readIdentityFields = () => controlFields(ReviewPageFieldCollector.collectIdentity(
  activeReviewRoot ?? document, manifestIdentityAnchors() ?? [],
));

const originalIdentityFields = (fields: Record<string, unknown>) => {
  const original = { ...fields };
  for (const [field, write] of verifiedFieldWrites) {
    if (original[field] === write.value) original[field] = write.original;
  }
  return original;
};

const sameCollectedRecord = (message: ReviewMessage, fields?: Record<string, unknown>) => {
  if (!sameReviewStep() || message.expectedPageUrl !== window.location.href || message.expectedPageInstanceId !== pageInstanceId || !message.expectedPageFingerprint) return false;
  return message.expectedPageFingerprint === pageFingerprint(originalIdentityFields(fields ?? readIdentityFields()));
};

const sameActiveCollection = (message: ReviewMessage) =>
  Boolean(message.expectedCollectionId) && message.expectedCollectionId === activeCollectionId;

const sameReviewIdentity = (message: ReviewMessage, fields?: Record<string, unknown>) => sameActiveCollection(message) && sameCollectedRecord(message, fields);
// The target field may have been normalized by a framework after collection;
// identity remains valid when every anchor except that field is unchanged.
const sameReviewIdentityExceptFields = (message: ReviewMessage, fields: string[]) => {
  if (!sameReviewStep() || message.expectedPageUrl !== window.location.href || message.expectedPageInstanceId !== pageInstanceId || message.expectedCollectionId !== activeCollectionId) return false;
  const current = readIdentityFields();
  if (sameReviewIdentity(message, current)) return true;
  return sameRecordExceptFields(message.expectedPageFingerprint || "", pageFingerprint(current), fields,
    manifestRecordIdentityFields() ?? undefined);
};

const sameReviewIdentityExceptField = (message: ReviewMessage, field: string) => sameReviewIdentityExceptFields(message, [field]);

/**
 * 找到要定位的 <img>。
 *
 * 采集时缓存的节点优先；框架重渲染会换掉 `<img>` 节点（上传组件刷新、
 * Ant Design 重新挂载缩略图），此时按**采集时记录的图片地址**在页面上重新
 * 找一次——地址没变就还是同一张图。地址也找不到才算原图消失。
 */
const resolveReviewImage = (imageId: unknown) => {
  const snapshot = reviewImageElements.get(imageId);
  const collected = snapshot?.image;
  if (collected?.isConnected && (collected.currentSrc || collected.src) === snapshot.src) {
    return collected;
  }
  const src = snapshot?.src;
  if (!src) return null;
  return Array.from(document.images).find((image) => (image.currentSrc || image.src) === src) || null;
};

const focusReviewImage = (message: ReviewMessage) => {
  if (!sameCollectedRecord(message) || !message.expectedCollectionId || message.expectedCollectionId !== activeCollectionId) {
    return { ok: false, error: "页面已变化，请重新采集" };
  }
  const image = resolveReviewImage(message.imageId);
  if (!image) {
    return { ok: false, error: "原图已不在页面上，请重新采集" };
  }
  return ReviewImageFocus.focus(image);
};

const isCollectionMessage = (message: ReviewMessage) =>
  message?.type === MESSAGE_TYPES.collectPageData
  || message?.type === MESSAGE_TYPES.collectPageManifest;

const modelFiller = createVehicleModelFiller({
  root: document,
  isCurrent: message => activeCollectionBusiness === "vehicle_source" && sameReviewIdentity(message),
  collectionId: () => activeCollectionId,
  fields: reviewFieldElements,
  collect: fields => ReviewPageFieldCollector.collect(activeReviewRoot ?? document, new Set(fields)),
  writableFields,
  controlKinds: writableControlKinds,
  mappings: () => manifestActions().find(a => a.handler_id === "vehicle_model_fill" && a.enabled)?.mappings ?? [],
  fieldLabels: () => manifestFieldLabels() ?? {},
});

chrome.runtime.onMessage.addListener((message: ReviewMessage, _sender, sendResponse) => {
  if (message.type === "APPLY_VEHICLE_MODEL") {
    void modelFiller.fill(message).then(sendResponse);
    return true;
  }
  if (message.type === "DISCOVER_CONFIGURATION_PAGE" || message.type === "TRIAL_CONFIGURATION_PAGE") {
    const task = message.type === "DISCOVER_CONFIGURATION_PAGE" ? captureConfigurationPage()
      : trialConfigurationPage(message.task as ConfigurationPending);
    void task.then(sendResponse).catch(error => sendResponse({error: error instanceof Error ? error.message : "页面采集失败"}));
    return true;
  }
  if (message.type === MESSAGE_TYPES.applyPageFieldValue || message.type === MESSAGE_TYPES.applyPageFieldGroupValue) {
    const reply = sendResponse;
    const started = Date.now();
    sendResponse = (response) => {
      logDiagnostic("write.complete", { collectionId: message.expectedCollectionId, field: message.action?.field,
        ok: Boolean(response?.ok), code: response?.code, durationMs: Date.now() - started });
      reply(response);
    };
  }
  if (message.type === MESSAGE_TYPES.verifyInvoice) {
    if (!sameReviewIdentity(message)) {
      sendResponse({ ok: false, message: "页面已变化，请重新审核" });
      return true;
    }
    const field = message.action?.field;
    const entry = field && reviewFieldElements.get(field);
    if (!entry?.element || entry.collectionId !== activeCollectionId
      || typeof message.action?.expectedValue !== "string"
      || message.action.expectedValue !== entry.collectedValue
      || (entry.controlSnapshot && ReviewPageFieldWriter.captureValue(entry.element)?.control !== entry.controlSnapshot.control)) {
      sendResponse({ ok: false, message: "发票号码控件或原值已变化，请重新采集后验真" });
      return true;
    }
    sendResponse(ReviewPageFieldWriter.triggerInvoiceVerification(document, entry.element, entry.controlSnapshot?.value ?? entry.collectedValue));
    return;
  }
  if (message.type === MESSAGE_TYPES.setAffiliation) {
    if (activeCollectionBusiness !== "scrap_replacement" || !sameReviewIdentity(message)) {
      sendResponse({ ok: false, message: "页面已变化，请重新审核", actions: [] });
      return true;
    }
    const value = message.action?.value === "个人" || message.action?.value === "公司"
      ? message.action.value
      : null;
    if (!value) {
      sendResponse({ ok: false, message: "挂靠回填值缺少明确的主体材料依据", actions: [] });
      return true;
    }
    const fields = ["old_vehicle.affiliation", "new_vehicle.affiliation"];
    const targets = ReviewPageFieldCollector.collectActionTargets(activeReviewRoot ?? document, fields);
    const missing = targets.filter((item) => !item.element || item.ambiguous);
    if (missing.length) {
      sendResponse({
        ok: false,
        message: `挂靠字段无法唯一定位：${missing.map((item) => item.field).join("、")}`,
        actions: [],
      });
      return true;
    }
    const action = {
      fields,
      value,
      expectedValues: {},
    } satisfies PageWriteGroupAction;
    ReviewPageFieldWriter.executeValueGroup(
      document,
      targets.map((item) => ({ field: item.field, element: item.element! })),
      action,
      () => sameReviewIdentity(message),
      { fields: writableFields(), controlKinds: writableControlKinds() },
    ).then(sendResponse).catch((error) => sendResponse({
      ok: false,
      message: error instanceof Error ? error.message : "挂靠字段回填失败",
      actions: [],
    }));
    return true;
  }
  if (message.type === MESSAGE_TYPES.applyPageFieldValue) {
    const action = message.action;
    if (!action?.field || typeof action.value !== "string") {
      sendResponse({ ok: false, message: "字段回填参数不完整", actions: [] });
      return true;
    }
    if (!sameReviewIdentityExceptField(message, action.field)) {
      sendResponse({ ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新审核", actions: [] });
      return true;
    }
    if (!writableFields().has(action.field)) {
      sendResponse({ ok: false, message: "该字段不在允许回填范围内", actions: [] });
      return true;
    }
    const entry = reviewFieldElements.get(action.field);
    if (!entry?.element || entry.collectionId !== activeCollectionId) {
      sendResponse({ ok: false, message: "未找到该字段的当前页面控件", actions: [] });
      return true;
    }
    // Bind expected values to the actual input captured with this collection,
    // not wrapper text displayed beside it (validation badges/overlays).
    const writeAction = { ...action, field: action.field, value: action.value };
    if (entry.controlSnapshot && writeAction.expectedValue === entry.collectedValue) {
      writeAction.expectedValue = entry.controlSnapshot.value;
    }
    if (entry.controlSnapshot && ReviewPageFieldWriter.captureValue(entry.element)?.control !== entry.controlSnapshot.control) {
      sendResponse({ ok: false, code: "FIELD_TARGET_CHANGED", message: "该字段控件已重新加载，请重新采集后回填", actions: [] });
      return true;
    }
    const writeGuard = () => {
      const currentFields = readIdentityFields();
      if (sameReviewIdentity(message, currentFields)) return true;
      // A successful write legitimately changes the target field used by the
      // page fingerprint. Validate all other anchors and the expected value,
      // while substituting the value we just wrote for that one field.
      if (String(currentFields[action.field!] ?? "").trim() !== action.value!.trim()) return false;
      const expectedFields = originalIdentityFields(currentFields);
      const previousWrite = verifiedFieldWrites.get(action.field!);
      expectedFields[action.field!] = previousWrite ? previousWrite.original : action.expectedValue;
      return message.expectedPageUrl === window.location.href
        && sameReviewStep()
        && message.expectedPageInstanceId === pageInstanceId
        && message.expectedCollectionId === activeCollectionId
        && message.expectedPageFingerprint === pageFingerprint(expectedFields);
    };
    ReviewPageFieldWriter.executeValue(document, entry.element, writeAction, writeGuard, {
      fields: writableFields(),
      controlKinds: writableControlKinds(),
    })
      .then((result) => {
        if (result.ok) rememberVerifiedWrite(writeAction.field, action.expectedValue, result.actions?.[0]?.value ?? writeAction.value);
        if (result.ok || result.code === "FIELD_VALUE_CHANGED") {
          entry.controlSnapshot = ReviewPageFieldWriter.captureValue(entry.element);
          entry.collectedValue = result.ok ? result.actions?.[0]?.value : result.currentValue;
        }
        sendResponse(result);
      })
      .catch((error) => sendResponse({ ok: false, message: error instanceof Error ? error.message : "字段回填失败", actions: [] }));
    return true;
  }
  if (message.type === MESSAGE_TYPES.applyPageFieldGroupValue) {
    const action = message.action as PageWriteGroupAction | undefined;
    const fields = action?.fields || [];
    if (!action || fields.length < 2 || new Set(fields).size !== fields.length || typeof action.value !== "string") {
      sendResponse({ ok: false, message: "组合字段回填参数不完整", actions: [] });
      return true;
    }
    if (!sameReviewIdentityExceptFields(message, fields)) {
      sendResponse({ ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新审核", actions: [] });
      return true;
    }
    const entries = fields.map((field) => ({ field, entry: reviewFieldElements.get(field) })).map(({ field, entry }) => ({
      field,
      element: entry?.element,
      expectedValue: action.expectedValues?.[field] ?? entry?.collectedValue,
      entry,
    }));
    // 组合回填要同时满足「两个控件都找得到、都属于本次采集、都在业务白名单里」，
    // 任一不满足以前都只回一句笼统的话，四种原因在界面上长得一模一样。把原因
    // 逐条写进**面板提示行**——审核员看的就是那里，不该为了排查再去开 Console。
    const problems = entries
      .map((item) => ({
        field: item.field,
        reasons: [
          !item.element && "页面上没找到这个控件",
          item.entry?.collectionId !== activeCollectionId && "不属于本次采集",
          !writableFields().has(item.field) && "不在允许回填的字段清单里",
        ].filter((reason): reason is string => Boolean(reason)),
      }))
      .filter((item) => item.reasons.length);
    logDiagnostic("write.group.start", { collectionId: activeCollectionId, fieldCount: fields.length, problemCount: problems.length });
    if (problems.length) {
      sendResponse({
        ok: false,
        message: `组合字段无法回填：${problems.map((item) => `${item.field}（${item.reasons.join("、")}）`).join("；")}。请重新采集后重试`,
        actions: [],
      });
      return true;
    }
    if (entries.some((item) => item.entry.controlSnapshot && ReviewPageFieldWriter.captureValue(item.element)?.control !== item.entry.controlSnapshot.control)) {
      sendResponse({ ok: false, code: "FIELD_TARGET_CHANGED", message: "组合字段控件已重新加载，请重新采集后回填", actions: [] });
      return true;
    }
    ReviewPageFieldWriter.executeValueGroup(
      document,
      entries.map((item) => ({ field: item.field, element: item.element, expectedValue: item.expectedValue })),
      action,
      () => sameReviewIdentityExceptFields(message, fields),
      // 与单字段回填同一份白名单，不再退回内置的报废置换表。
      { fields: writableFields(), controlKinds: writableControlKinds() },
    ).then((result) => {
      if (result.ok) {
        for (const item of entries) {
          const filled = result.actions?.find((candidate) => candidate.field === item.field);
          if (filled) {
            rememberVerifiedWrite(item.field, item.expectedValue, filled.value);
            item.entry.controlSnapshot = ReviewPageFieldWriter.captureValue(item.element);
            item.entry.collectedValue = filled.value;
          }
        }
      }
      logDiagnostic("write.group.complete", { collectionId: activeCollectionId, ok: result.ok, code: result.code, actionCount: result.actions?.length ?? 0 });
      sendResponse(result);
    }).catch((error) => {
      logDiagnostic("write.group.failed", { collectionId: activeCollectionId, errorType: error instanceof Error ? error.name : "unknown" });
      sendResponse({ ok: false, message: error instanceof Error ? error.message : "组合字段回填失败", actions: [] });
    });
    return true;
  }
  if (message.type === MESSAGE_TYPES.focusReviewImage) {
    sendResponse(focusReviewImage(message));
    return;
  }
  if (!isCollectionMessage(message)) {
    return;
  }

  const collectionId = nextCollectionId();
  modelFiller.invalidate();
  latestCollectionId = collectionId;

  const log = (event: string, details: Record<string, unknown> = {}) => {
    logDiagnostic(`collect.${event}`, { collectionId, ...details });
  };
  log("start");

  const allText = document.body.innerText || "";
  // 页面识别清单要先应用：识别发生在按业务类型取采集清单之前，识别不出来
  // 就不知道该取哪份清单。清单不可用时识别退回内置表，对共享地址只报
  // 「认不出来」，不猜。
  applyPageCatalog(message.pageCatalog as PageCatalog | null | undefined);
  const tabLabels = reviewTabLabels(effectivePageCatalog(), window.location.href);
  const reviewTab = readActiveReviewTab(document, tabLabels);
  const businessResolution = ReviewBusinessDetector.resolve(
    window.location.href,
    allText,
    message.businessSelection as BusinessSelection | null | undefined,
    reviewTab,
  );
  const business = businessResolution.business;
  // 替换采集时断开旧监听；报废置换没有页签声明，继续使用原有路径及身份守卫。
  if (reviewTabGuard) activeCollectionId = "";
  reviewTabGuard?.disconnect();
  activeReviewRoot = business ? reviewTabCollectionRoot(reviewTab) : null;
  const collectionStepGuard = activeReviewRoot ? guardActiveReviewTab(document, tabLabels, reviewTab, () => {
    if (activeCollectionId === collectionId) activeCollectionId = "";
    if (latestCollectionId === collectionId) latestCollectionId = "";
  }) : null;
  reviewTabGuard = collectionStepGuard;
  // Record identity resolution counts, without page paths or page text.
  const diagnostics = explainPageIdentity(effectivePageCatalog(), window.location.href, allText, reviewTab);
  log("business", {
    business: business?.businessType ?? null,
    region: business?.region ?? null,
    mode: business?.selectionMode ?? null,
    failed: Boolean(businessResolution.error),
    catalogApplied: Boolean(appliedPageCatalog()?.identities.length),
    candidateCount: diagnostics.byPath.length,
    matchedCount: diagnostics.matched.length,
  });
  // 采集清单按业务类型选择；没有可用清单时清空并退回内置表。
  applyCollectManifest(
    (message.collectManifests as CollectManifest[] | undefined)
      ?.find((item) => item.business_type === business?.businessType &&
        (!item.page_id || item.page_id === business?.pageId) &&
        (!item.region || item.region === business?.region) && (!item.profile_version || item.profile_version === business?.profileVersion)),
  );
  const fieldCollection = ReviewPageFieldCollector.collect(activeReviewRoot ?? document);
  const pageFields = controlFields(fieldCollection);
  const controlSnapshots = new Map(fieldCollection.fieldTargets.map(({ field, element }) =>
    [field, ReviewPageFieldWriter?.captureValue?.(element) ?? null]));
  const reviewFields = (fieldCollection.reviewFields || []).map((item) =>
    item.field && controlSnapshots.get(item.field) ? { ...item, value: pageFields[item.field] } : item);
  const writableTargets = fieldCollection.writableTargets;
  const unmatchedLabels = fieldCollection.unmatchedLabels;
  // DOM 元素只保留在 Content Script 内部；跨消息只发送字段键和可定位状态。
  const fieldTargets = fieldCollection.fieldTargets.map(({ field }) => ({
    field,
    present: true,
  }));
  log("fields", {
    scannedControls: fieldCollection.scannedControls,
    matchedFields: Object.keys(pageFields).length,
    reviewFieldCount: reviewFields.length,
    unmatchedLabelCount: unmatchedLabels.length,
    candidateCount: fieldCollection.candidateCount,
    ambiguousFieldCount: fieldCollection.ambiguousFields.length
  });

  const imageCandidates = collectImageCandidates(classifyImage);
  const selection = ReviewImageCandidates.select(imageCandidates, MAX_REVIEW_IMAGES);
  // Counts only; labels can contain document values and must not enter Console.
  log("images", {
    scanned: selection.scannedCount,
    eligible: selection.eligibleCount,
    selectedCount: selection.selected.length,
    unknownTypeCount: selection.selected.filter((candidate) => candidate.categoryHint === "unknown").length,
  });
  const images = selection.selected;
  const pageSnapshot = {
    ...(business || {}), pageUrl: window.location.href, pageId: business?.pageId,
    pageInstanceId, pageFingerprint: pageFingerprint(pageFields), collectionId,
    pageTitle: document.title, pageFields, reviewFields,
    businessDetectionError: businessResolution.error,
  };
  const fieldDiagnostics = {
    scannedControls: fieldCollection.scannedControls, matchedFields: Object.keys(pageFields).length,
    reviewFieldCount: reviewFields.length, unmatchedLabels,
    candidateCount: fieldCollection.candidateCount, ambiguousFields: fieldCollection.ambiguousFields,
  };
  const collectionIssues = selection.overflow
    ? [`候选资料超过 ${MAX_REVIEW_IMAGES} 张，请确认是否漏审`] : [];
  const collectionPayload = {writableTargets, fieldTargets, pageText: allText};
  Promise.resolve().then(() => images.map(imageAssetMetadata))
    .then((imageAssets) => {
      collectionStepGuard?.isCurrent();
      if (latestCollectionId !== collectionId) {
        // 被取代的采集也要带回业务识别结果和真实原因，面板不得退化为“无法识别业务”。
        sendResponse({
          ...pageSnapshot, images: [], staleCollection: true,
          collectionIssues: ["页面采集已过期，请重新采集"],
          collectionDiagnostics: { imageSuccessCount: 0, imageFailureCount: 0 },
        });
        return;
      }
      // The active map changes only with the response that owns this token.
      reviewImageElements.clear();
      images.forEach((candidate, index) => {
        const imageAsset = imageAssets[index];
        if (imageAsset?.src) reviewImageElements.set(candidate.imageId, { image: candidate.image, src: imageAsset.src });
      });
      reviewFieldElements.clear();
      verifiedFieldWrites.clear();
      for (const { field, element } of fieldCollection.fieldTargets) {
        reviewFieldElements.set(field, { element, collectionId, collectedValue: pageFields[field] ?? null,
          controlSnapshot: controlSnapshots.get(field) ?? null });
      }
      activeCollectionId = collectionId;
      activeCollectionBusiness = business?.businessType || "";
      const imageFailureCount = imageAssets.filter((image) => image.collectionError).length;
      const collectionDiagnostics = {
        ...fieldDiagnostics,
        imageSuccessCount: imageAssets.length - imageFailureCount,
        imageFailureCount,
        scannedImages: selection.scannedCount,
        selectedImages: imageAssets.length,
        imageOverflow: selection.overflow,
        collectionIssues
      };
      log("complete", {
        imageCount: imageAssets.length,
        imageFailureCount,
        fieldCount: Object.keys(pageFields).length,
        scannedImages: selection.scannedCount,
        selectedImages: imageAssets.length,
        imageOverflow: selection.overflow
      });
      sendResponse({
        ...pageSnapshot, ...collectionPayload, images: imageAssets,
        collectionIssues, collectionDiagnostics,
      });
    })
    .catch((error) => {
      log("failed", { errorType: error instanceof Error ? error.name : "unknown" });
      sendResponse({
        ...pageSnapshot, ...collectionPayload, images: [],
        collectionIssues: [error instanceof Error ? error.message : "图片采集失败"],
        collectionDiagnostics: {...fieldDiagnostics, imageSuccessCount: 0, imageFailureCount: 0},
      });
    });
  return true;
});

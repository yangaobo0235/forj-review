import type { DomElement, DomRoot, FieldDefinition, FieldCandidate } from "../dom.ts";
import { antDatePicker, formControlKind, isSelectControl, logicalFormControls, readFormControlValue } from "../form-controls.ts";
import { manifestSectionFromText } from "../catalog/collect-manifest.ts";
import { fieldDefinitions, definitionsByLabel, definitionForLabel, isKnownLabel, isSectionRequired, normalizeText } from "../catalog/field-definitions.ts";
import type { ReviewFieldSnapshot } from "../../../shared/contracts/review.ts";

const isDomElement = (value: unknown): value is DomElement => {
if (!value || typeof value !== "object") return false;
const candidate = value as { nodeType?: number; tagName?: string };
return candidate.nodeType === 1 || typeof candidate.tagName === "string";
};

/**
 * 功能：从表单、表格和只读 DOM 提取标准字段候选。
 * 职责边界：同等可靠候选冲突时保留歧义，不猜测值；只有唯一候选才保留 DOM 目标。
 * 修改日期：2026-09-11
 */


const SOURCE_SCORES: Record<string, number> = {
  control: 400,
  table: 350,
  structured: 300,
  inline: 200,
  adjacent: 100,
};
// Ant Design's a-select exposes an input role=combobox; some deployed
// shells put the role on the trigger itself.
const CONTROL_SELECTOR = "input, textarea, select, [role='combobox'], input[aria-controls], input[aria-owns], [contenteditable]:not([contenteditable='false'])";
const FORM_ITEM_SELECTOR = ".ant-form-item, .el-form-item, .form-item, [data-form-item], [data-index][class*='form'], [class*='FormItem'], [class*='formItem']";
const IGNORED_CONTROL_LABELS = new Set(["报废车挂靠", "新车挂靠"]);
const SYSTEM_CONTROL_LABELS = new Set(["审核状态", "审核进度", "审核结果"]);

const PLACEHOLDER_VALUES = new Set(["审核进度", "待审核", "审核中", "处理中", "未审核", "请选择", "请输入"]);
const isUsableValue = (value: unknown) => {
  const normalized = normalizeText(value);
  return Boolean(normalized) && !PLACEHOLDER_VALUES.has(normalized);
};
const isDataControl = (control: DomElement | null | undefined) => {
  const type = String(control?.getAttribute?.("type") || "text").toLowerCase();
  return !["hidden", "button", "submit", "reset", "image"].includes(type);
};
const isVerificationCandidate = (candidate: FieldCandidate) => {
  const label = normalizeText(candidate?.label);
  const value = normalizeText(candidate?.value);
  return /验真/.test(label) || (/(发票号码|发票代码)/.test(label) && /^(?:一键)?验真$/.test(value));
};
const visibleText = (element: DomElement | null | undefined) => String(element?.innerText ?? element?.textContent ?? "").trim();

const directText = (element: DomElement | null | undefined) => {
  const fixtureText = element?.getAttribute?.("data-direct-text");
  if (fixtureText) return fixtureText.trim();
  if (!element?.childNodes) return visibleText(element);
  return Array.from(element.childNodes)
    .filter((node) => node.nodeType === 3)
    .map((node) => node.textContent || "")
    .join(" ")
    .trim();
};

const sectionFromText = (text: string) => {
  const value = String(text || "");
  const declared = manifestSectionFromText(value);
  if (declared) return declared;
  const hasOld = /报废车辆信息|报废车辆资料|报废车资料|旧车资料/.test(value);
  const hasNew = /新车及发票信息|新车及发票资料|新车资料|发票信息/.test(value);
  if (hasOld && !hasNew) return "old_vehicle";
  if (hasNew && !hasOld) return "new_vehicle";
  return "unknown";
};

const isSearchFilterControl = (control: DomElement | null | undefined) => Boolean(
  control?.closest?.<DomElement>(".smallForm, .formInline, [class*='formInline']"),
);

const isSystemControlLabel = (label: string) => {
  const normalized = normalizeText(label);
  return [...SYSTEM_CONTROL_LABELS].some((systemLabel) =>
    normalized === systemLabel || normalized.startsWith(systemLabel),
  );
};

const isIgnoredControlLabel = (label: string) => {
  const normalized = normalizeText(label);
  return [...IGNORED_CONTROL_LABELS].some((ignored) =>
    normalized === ignored || normalized.startsWith(ignored),
  );
};

const normalizeCollectedFieldValue = (field: string | null | undefined, value: unknown) => {
  const raw = String(value ?? "").trim();
  return field === "invoice.amount" && /^[￥¥\s]*$/.test(raw) ? "" : raw;
};


const queryAll = (root: DomRoot | null | undefined, selector: string) => {
  try {
    return Array.from(root?.querySelectorAll?.<DomElement>(selector) || []);
  } catch {
    return [];
  }
};

const readControlValue = readFormControlValue;

const findSection = (element: DomElement | null | undefined) => {
  const fixtureSection = element?.dataset?.reviewSection;
  if (fixtureSection && fixtureSection !== "unknown") return fixtureSection;

  // Stop before the document body: broad container text can mention both old
  // and new vehicles and would incorrectly assign repeated labels.
  let current = element;
  const visited = new Set<DomElement>();
  for (; current && !visited.has(current); current = current.parentElement) {
    visited.add(current);
    if (["BODY", "HTML"].includes(current.tagName)) break;
    const ownSection = sectionFromText(
      `${directText(current)} ${current.getAttribute?.("aria-label") || ""}`,
    );
    if (ownSection !== "unknown") return ownSection;

    // 组件包装深度不构成业务边界。允许穿过 MyForm/Ant 控件的任意包装，
    // 但不得越过所属表单或页签去继承另一个审核区的标题。
    if (current.tagName === "FORM" || current.getAttribute?.("role") === "tabpanel") break;

    // MyForm 将标题和字段渲染为同一行内的兄弟列，而非标题包裹字段。
    // 只沿本级之前的明确标题找分区；遇到未知标题也停止，避免跨区继承。
    const siblings = directElementChildren(current.parentElement);
    for (let index = siblings.indexOf(current) - 1; index >= 0; index -= 1) {
      const sibling = siblings[index];
      const title = /^(H[1-6]|LEGEND)$/.test(sibling.tagName)
        || sibling.classList?.contains("formItemTitle") ? sibling
        : sibling.querySelector?.<DomElement>(".formItemTitle, h1, h2, h3, h4, h5, h6, legend");
      if (title) return sectionFromText(visibleText(title));
    }

    const headingText = directElementChildren(current)
      .filter((child) => /^(H[1-6]|LEGEND)$/.test(child.tagName))
      .map(visibleText)
      .join(" ");
    const headingSection = sectionFromText(headingText);
    if (headingSection !== "unknown") return headingSection;

    const shortChildTitleText = directElementChildren(current)
      .map(visibleText)
      .filter((text) => text.length > 0 && text.length <= 40)
      .join(" ");
    const childTitleSection = sectionFromText(shortChildTitleText);
    if (childTitleSection !== "unknown") return childTitleSection;
  }
  return "unknown";
};

const controlLabel = (root: DomRoot | null | undefined, control: DomElement) => {
  const labels: string[] = [];
  const formItem = control.closest?.<DomElement>(FORM_ITEM_SELECTOR);
  const wrappingLabel = control.closest?.<DomElement>("label");
  const itemLabel = formItem?.querySelector?.<DomElement>(
    ".ant-form-item-label label, .el-form-item__label, label",
  );
  labels.push(
    visibleText(itemLabel),
    visibleText(wrappingLabel),
  );
  // 两张发票可能复用 input id。所属表单项的标签优先，不能拿另一分区的
  // 第一个 label 覆盖当前控件。没有本地标签时只接受唯一的 for 关联。
  if (control.id && root?.querySelectorAll) {
    const escapedId = globalThis.CSS?.escape ? globalThis.CSS.escape(control.id) : control.id;
    const associated = queryAll(root, `label[for="${escapedId}"]`);
    if (associated.length === 1) labels.push(visibleText(associated[0]));
  }
  labels.push(
    control.getAttribute?.("aria-label") || "",
    control.getAttribute?.("name") || "",
  );
  // Use one authoritative association. Concatenating for/item/aria labels
  // downgrades a control to a fuzzy match and lets its wrapper text win.
  return (labels.find((label) => String(label).trim())?.trim() || "")
    .replace(/\s*(?:不一致|易混淆|一致)\s*$/, "")
    .trim();
};

const controlCandidates = (root: DomRoot | null | undefined, controls: DomElement[] = queryAll(root, CONTROL_SELECTOR)) =>
  controls
    .filter(isDataControl)
    .flatMap((control) => {
      const label = controlLabel(root, control);
      const value = readControlValue(control);
      const section = findSection(control);
      const candidate = {
        label,
        value,
        section,
        source: "control",
        element: control,
        proximity: visibleText(control.parentElement).length,
      };
      const nearbyText = visibleText(control.parentElement) + visibleText(control.parentElement?.parentElement);
      const controlType = String(control.getAttribute?.("type") || "").toLowerCase();
      const isDateControl = controlType === "date" || /^\d{4}[-/]\d{1,2}[-/]\d{1,2}$/.test(String(value));
      const fallback = isDateControl && /开票日期|发票日期/.test(`${label}${nearbyText}`)
        ? [{ ...candidate, label: "开票日期", source: "control-fallback" }]
        : [];
      return [candidate, ...fallback];
    })
    .filter((candidate) => candidate.label && (
      isUsableValue(candidate.value) ||
      isKnownLabel(candidate.label)
    ))
    .filter((candidate) => !isIgnoredControlLabel(candidate.label))
    .filter((candidate) => !isVerificationCandidate(candidate));

const isVisibleControl = (control: DomElement | null | undefined) => {
  const type = String(control?.getAttribute?.("type") || "").toLowerCase();
  if (["hidden", "button", "submit", "reset", "image"].includes(type)) return false;
  if (control?.hidden === true || control?.getAttribute?.("aria-hidden") === "true") return false;
  for (let node = control; node; node = node.parentElement) {
    if (node.hidden === true || node.getAttribute?.("aria-hidden") === "true") return false;
    if (node.style?.display === "none" || node.style?.visibility === "hidden") return false;
    const getComputedStyle = node.ownerDocument?.defaultView?.getComputedStyle;
    if (typeof getComputedStyle === "function") {
      const style = getComputedStyle.call(node.ownerDocument.defaultView, node);
      if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse") return false;
    }
  }
  return true;
};

const isEditableControl = (control: DomElement | null | undefined) => {
  if (!isVisibleControl(control) || control?.disabled === true || control?.getAttribute?.("aria-disabled") === "true") return false;
  // readonly picker inputs are still editable through their calendar. Disabled
  // wrappers remain non-editable, including controls disabled by a fieldset.
  for (let node = control; node; node = node.parentElement) {
    if (node.disabled === true || node.getAttribute?.("aria-disabled") === "true"
      || node.classList?.contains("ant-picker-disabled") || node.classList?.contains("ant-select-disabled")) return false;
  }
  const customEditable = isSelectControl(control) || Boolean(antDatePicker(control)) || control?.getAttribute?.("contenteditable") === "true";
  return customEditable || (control?.readOnly !== true && control?.getAttribute?.("readonly") == null);
};

const controlType = formControlKind;

const displayLabel = (label: string) => String(label || "")
  .replace(/[＊*]/g, "")
  .replace(/[：:]\s*$/, "")
  .replace(/\s+/g, " ")
  .trim();

const readInventoryValue = readFormControlValue;

const directElementChildren = (element: DomElement | null | undefined) => Array.from(element?.children || []).filter(isDomElement);

const structuredCandidatesForContainer = (container: DomElement) => {
  // A form value comes from the live control, never its decorated wrapper
  // (validation badges, duplicated highlight overlays, buttons, etc.).
  if (queryAll(container, CONTROL_SELECTOR).some(isDataControl)) return [];
  const children = directElementChildren(container);
  const cells = children.filter((child) => ["TH", "TD"].includes(child.tagName));
  if (cells.length >= 2) {
    const candidates = [];
    for (let index = 0; index + 1 < cells.length; index += 2) {
      candidates.push({
        label: visibleText(cells[index]),
        value: visibleText(cells[index + 1]),
        section: findSection(container),
        source: "table",
        element: cells[index + 1],
        proximity: visibleText(container).length,
      });
    }
    return candidates;
  }

  const labelNode = container.querySelector?.<DomElement>(
    ":scope > dt, :scope > label, .ant-descriptions-item-label, .el-descriptions__label, .ant-form-item-label, .el-form-item__label",
  );
  const valueNode = container.querySelector?.<DomElement>(
    ":scope > dd, .ant-descriptions-item-content, .el-descriptions__content, .ant-form-item-control, .el-form-item__content, .field-value, .value",
  );
  if (labelNode && valueNode) {
    return [{
      label: visibleText(labelNode),
      value: visibleText(valueNode),
      section: findSection(container),
      source: "structured",
      element: valueNode,
      proximity: visibleText(container).length,
    }];
  }

  if (children.length === 2 && isKnownLabel(visibleText(children[0]))) {
    return [{
      label: visibleText(children[0]),
      value: visibleText(children[1]),
      section: findSection(container),
      source: "structured",
      element: children[1],
      proximity: visibleText(container).length,
    }];
  }
  return [];
};

const structuredCandidates = (root: DomRoot | null | undefined) =>
  queryAll(
    root,
    `tr, dl > div, .ant-descriptions-item, .el-descriptions__row, ${FORM_ITEM_SELECTOR}`,
  )
    .flatMap(structuredCandidatesForContainer)
    .filter((candidate) => candidate.label && isUsableValue(candidate.value));

const adjacentCandidates = (root: DomRoot | null | undefined) => {
  const candidates = [];
  for (const element of queryAll(root, "body *, *")) {
    const ownText = directText(element);
    const inlineMatch = ownText.match(/^\s*([^：:]+)[：:]\s*(.+)\s*$/);
    if (inlineMatch && isKnownLabel(inlineMatch[1])) {
      candidates.push({
        label: inlineMatch[1],
        value: inlineMatch[2],
        section: findSection(element),
        source: "inline",
        element,
        proximity: ownText.length,
      });
      continue;
    }
    if (!isKnownLabel(ownText)) continue;
    if (queryAll(element.nextElementSibling, CONTROL_SELECTOR).some(isDataControl)) continue;
    const value = visibleText(isDomElement(element.nextElementSibling) ? element.nextElementSibling : null);
    if (!isUsableValue(value) || value.length > 200) continue;
    candidates.push({
      label: ownText,
      value,
      section: findSection(element),
      source: "adjacent",
      element: isDomElement(element.nextElementSibling) ? element.nextElementSibling : null,
      proximity: value.length,
    });
  }
  return candidates;
};

const matchingScore = (definition: FieldDefinition, candidate: FieldCandidate, scopedControls: DomElement[] = []) => {
  const label = normalizeText(candidate.label);
  if (definition.locatorId && candidate.element?.id !== definition.locatorId) return null;
  if (definition.locatorName && candidate.element?.getAttribute("name") !== definition.locatorName) return null;
  if (definition.occurrence && candidate.element && !definition.locatorId && !definition.locatorName) {
    // occurrence 按本次审核详情中的控件计数，列表筛选框和隐藏控件不参与定位。
    const equivalent = scopedControls.filter(el => normalizeText(controlLabel(el.ownerDocument, el)) === label && findSection(el) === candidate.section);
    if (equivalent.length && equivalent.indexOf(candidate.element) !== definition.occurrence - 1) return null;
  }
  let exact = true;
  let aliasIndex = definition.aliases.findIndex((alias) => normalizeText(alias) === label);
  if (aliasIndex < 0) {
    exact = false;
    aliasIndex = definition.aliases.findIndex((alias) => label.includes(normalizeText(alias)));
  }
  if (aliasIndex < 0) return null;
  if (!String(candidate.value || "").trim() && !String(candidate.source || "").startsWith("control")) return null;
  if (
    candidate.section !== "unknown" &&
    candidate.section !== definition.section
  ) return null;
  const matchedAlias = normalizeText(definition.aliases[aliasIndex]);
  const aliasFieldCount = definitionsByLabel().get(matchedAlias)?.length ?? 0;
  if (
    aliasFieldCount > 1 &&
    candidate.section === "unknown"
  ) return null;

  return (
    (exact ? 10_000 : 5_000) -
    (aliasFieldCount > 1 ? 1_000 : 0) +
    (candidate.section === definition.section ? 500 : 0) +
    (SOURCE_SCORES[candidate.source] || 0)
  );
};

const resolveInventoryField = (candidate: FieldCandidate, scopedControls: DomElement[]) => {
  const ranked = Object.entries(fieldDefinitions())
    .map(([field, definition]) => ({
      field,
      definition,
      score: matchingScore(
        definition,
        { ...candidate, value: candidate.value || "__empty_control__" },
        scopedControls,
      ),
    }))
    .filter((item) => item.score != null)
    .sort((left, right) => (right.score ?? 0) - (left.score ?? 0));
  if (!ranked.length || ranked.filter((item) => item.score === ranked[0].score).length !== 1) return null;
  return ranked[0];
};

const reviewFieldInventory = (root: DomRoot | null | undefined, controls: DomElement[]) => {
  const seenLogicalControls = new Set();
  const inventory = [];
  for (const control of controls) {
    const label = controlLabel(root, control);
    if (!label) continue;
    if (isSearchFilterControl(control) || isSystemControlLabel(label) || isIgnoredControlLabel(label)) continue;
    const container = control.closest?.<DomElement>(FORM_ITEM_SELECTOR) || control;
    if (seenLogicalControls.has(container)) continue;
    seenLogicalControls.add(container);
    const candidate = {
      label,
      value: readInventoryValue(control),
      section: findSection(control),
      source: "control", element: control,
    };
    const resolved = resolveInventoryField(candidate, controls);
    if (resolved?.definition.reviewable === false) continue;
    // 隐藏控件不是当前详情字段；只读的已知字段（日期等）仍需进入审核目录，
    // 但会以 editable=false 传给后端，避免提供页面回写按钮。
    if (!isEditableControl(control) && !resolved) continue;
    inventory.push({
      field: resolved?.field || null,
      label: resolved?.definition.aliases[0] || displayLabel(label),
      value: normalizeCollectedFieldValue(resolved?.field, candidate.value),
      controlType: controlType(control),
      editable: isEditableControl(control),
      section: candidate.section,
      operationOnly: false,
    });
  }

  // Repeated controls with the same canonical field cannot be mapped safely.
  const mappedCounts = inventory.reduce((counts, item) => {
    if (item.field) counts.set(item.field, (counts.get(item.field) || 0) + 1);
    return counts;
  }, new Map());
  return inventory.map((item, index) => ({
    ...item,
    field: item.field && mappedCounts.get(item.field) === 1 ? item.field : null,
    order: index + 1,
  }));
};

const reviewRoot = (root: DomRoot | null | undefined) => {
  // 列表筛选区与审核详情区存在重复 id/label，优先采集实际审核详情弹层。
  // 没有打开详情时保留传入 root，兼容普通页面和测试 fixture。
  const modalCandidates = queryAll(root, ".my-page-modal").filter((item) =>
    item.querySelector?.<DomElement>(".largeForm"),
  );
  const modal = modalCandidates[modalCandidates.length - 1];
  if (modal) return modal.querySelector<DomElement>(".largeForm") || modal;
  const fallback = root?.querySelector?.<DomElement>(
    ".my-page-modal .largeForm, .my-page-modal-body .largeForm, .my-page-modal",
  );
  return fallback || root;
};

interface CandidateCollectionOptions {
  scannedControls?: number;
  reviewFields?: ReviewFieldSnapshot[];
  scopedControls?: DomElement[];
  onlyFields?: ReadonlySet<string>;
}

const collectCandidates = (candidates: FieldCandidate[], {
  scannedControls = 0, reviewFields = [], scopedControls = [], onlyFields,
}: CandidateCollectionOptions = {}) => {
  const pageFields: Record<string, string> = {};
  const fieldTargets = [];
  const unmatchedLabels = [];
  const ambiguousFields = [];
  for (const [field, definition] of Object.entries(fieldDefinitions())) {
    if (onlyFields && !onlyFields.has(field)) continue;
    const ranked = candidates
      .map((candidate) => ({
        candidate,
        score: matchingScore(definition, candidate, scopedControls),
      }))
      .filter((item) => item.score != null)
      .sort((left, right) => (right.score ?? 0) - (left.score ?? 0));

    if (!ranked.length) {
      unmatchedLabels.push(definition.aliases[0]);
      continue;
    }

    const top = ranked.filter((item) => item.score === ranked[0].score);
    // Preserve equally reliable candidates for manual review instead of guessing by value length.
    // Equally ranked duplicates stay ambiguous even when values agree, so a review
    // marker can never be mapped onto the wrong element.
    if (top.length > 1) {
      ambiguousFields.push(field);
      unmatchedLabels.push(definition.aliases[0]);
      continue;
    }
    const [accepted] = top;
    pageFields[field] = normalizeCollectedFieldValue(field, accepted.candidate.value);
    if (accepted.candidate.element) {
      fieldTargets.push({ field, element: accepted.candidate.element });
    }
  }

  if (fieldDefinitions()["invoice.invoice_date"] && (!onlyFields || onlyFields.has("invoice.invoice_date")) && !pageFields["invoice.invoice_date"]) {
    const dateCandidates = candidates.filter((candidate) =>
      /开票日期|发票日期/.test(normalizeText(candidate.label)) &&
      /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(String(candidate.value || "")),
    );
    const fallback = dateCandidates.find((candidate) => candidate.section === "new_vehicle")
      || dateCandidates.find((candidate) => candidate.section === "unknown");
    if (fallback) {
      pageFields["invoice.invoice_date"] = fallback.value;
      if (dateCandidates.length === 1 && fallback.element) {
        fieldTargets.push({ field: "invoice.invoice_date", element: fallback.element });
      }
      const unmatchedIndex = unmatchedLabels.indexOf("开票日期");
      if (unmatchedIndex >= 0) unmatchedLabels.splice(unmatchedIndex, 1);
    }
  }

  return {
    pageFields,
    fieldTargets,
    writableTargets: [],
    unmatchedLabels,
    ambiguousFields,
    candidateCount: candidates.length,
    scannedControls,
    reviewFields,
  };
};

const collect = (root: DomRoot | null | undefined, onlyFields?: ReadonlySet<string>) => {
  const scopedRoot = reviewRoot(root);
  const rawControls = logicalFormControls(queryAll(scopedRoot, CONTROL_SELECTOR).filter(isDataControl))
    .filter((control) => isDataControl(control) && isVisibleControl(control) && !isSearchFilterControl(control));
  const controls = controlCandidates(scopedRoot, rawControls);
  const reviewFields = onlyFields ? [] : reviewFieldInventory(scopedRoot, rawControls);
  const inSearchFilter = (candidate: FieldCandidate) => isSearchFilterControl(candidate.element);
  return collectCandidates(
    [
      ...controls,
      ...structuredCandidates(scopedRoot),
      ...adjacentCandidates(scopedRoot),
    ].filter((candidate) => !inSearchFilter(candidate) && !isSystemControlLabel(candidate.label) && !isIgnoredControlLabel(candidate.label) && !isVerificationCandidate(candidate)),
    {scannedControls: rawControls.length, reviewFields, scopedControls: rawControls, onlyFields},
  );
};

export const ReviewPageFieldCollector = {
  // Discovery uses raw candidates without canonical field definitions or a backend manifest.
  discover: (root: DomRoot) => {
    const scoped = reviewRoot(root);
    const controls = logicalFormControls(queryAll(scoped, CONTROL_SELECTOR).filter(isDataControl))
      .filter(control => isDataControl(control) && isVisibleControl(control) && !isSearchFilterControl(control));
    return [...controlCandidates(scoped, controls), ...structuredCandidates(scoped), ...adjacentCandidates(scoped)]
      .filter(candidate => candidate.label && !isSystemControlLabel(candidate.label)
        && !isIgnoredControlLabel(candidate.label) && !isVerificationCandidate(candidate)
        && !isSearchFilterControl(candidate.element));
  },
  collect,
  // 写回守卫只需要身份字段；仍实时解析 DOM 并保留重复目标的歧义检查。
  collectIdentity: (root: DomRoot, fields: readonly string[]) => collect(root, new Set(fields)),
  collectCandidates,
  isEditableControl,
  definitionForLabel,
  isSectionRequired,
  sectionFromText,
};

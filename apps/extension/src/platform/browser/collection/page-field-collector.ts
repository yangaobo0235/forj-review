import type { DomElement, DomRoot, FieldDefinition, FieldCandidate } from "../dom.ts";
import { manifestFieldDefinitions, manifestSectionFromText } from "../catalog/collect-manifest.ts";
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
 * 修改人：wuyi
 */


  const FIELD_DEFINITIONS: Record<string, FieldDefinition> = {
    "application.id": {
      aliases: ["申请单ID", "申请单编号"],
      section: "unknown",
    },
    "application.submitted_at": {
      aliases: ["申请时间"],
      section: "unknown",
      reviewable: false,
    },
    "application.owner_type": {
      aliases: ["车辆所有人类型"],
      section: "unknown",
    },
    "page_ocr.new_vehicle_vin": {
      aliases: ["OCR新车车架号"],
      section: "unknown",
    },
    "application.customer_name": {
      aliases: ["客户名称"],
      section: "unknown",
    },
    "old_vehicle.type": {
      aliases: ["报废车辆类型"],
      section: "old_vehicle",
    },
    "application.dealer_name": {
      aliases: ["经销商"],
      section: "old_vehicle",
      // 经销商由系统写死展示，不是审核详情中的可编辑/可核验控件。
      reviewable: false,
    },
    "old_vehicle.recycle_date": {
      aliases: ["报废交车日期", "报废车日期"],
      section: "old_vehicle",
    },
    "old_vehicle.vin": {
      aliases: ["报废车辆车架号", "旧车车架号", "车架号"],
      section: "old_vehicle",
      sectionRequired: true,
    },
    "old_vehicle.owner": {
      aliases: ["报废车辆所有人", "旧车所有人", "车辆所有人", "所有人"],
      section: "old_vehicle",
      sectionRequired: true,
    },
    "old_vehicle.plate_no": {
      aliases: ["报废车辆车牌号", "旧车车牌号", "车牌号"],
      section: "old_vehicle",
      sectionRequired: true,
    },
    "old_vehicle.engine_model": {
      aliases: ["报废发动机型号", "发动机型号"],
      section: "old_vehicle",
    },
    "scrap_certificate.certificate_no": {
      aliases: ["报废证明编号", "回收证明编号"],
      section: "old_vehicle",
    },
    "new_vehicle.vin": {
      aliases: ["新车车架号", "车架号"],
      section: "new_vehicle",
      sectionRequired: true,
    },
    "new_vehicle.owner": {
      aliases: ["新车所有人", "车辆所有人", "所有人"],
      section: "new_vehicle",
      sectionRequired: true,
    },
    "new_vehicle.plate_no": {
      aliases: ["新车车牌号", "车牌号"],
      section: "new_vehicle",
      sectionRequired: true,
    },
    "invoice.code": {
      aliases: ["发票代码"],
      section: "new_vehicle",
    },
    "invoice.invoice_no": {
      aliases: ["发票号码"],
      section: "new_vehicle",
    },
    "invoice.invoice_date": {
      aliases: ["开票日期", "发票日期"],
      section: "new_vehicle",
    },
    "invoice.amount": {
      aliases: ["开票金额"],
      section: "new_vehicle",
    },
    "new_vehicle.fuel_type": {
      aliases: ["新车燃料类型"],
      section: "new_vehicle",
    },
    "new_vehicle.registration_date": {
      aliases: ["注册日期"],
      section: "new_vehicle",
    },
    "application.terminal_certificate_no": {
      aliases: ["终端证件号"],
      section: "new_vehicle",
    },
    "application.terminal_phone": {
      aliases: ["终端客户手机号"],
      section: "new_vehicle",
    },
  };

  /**
   * 字段定义表：后端下发了采集清单就用清单，否则用内置表兜底。
   * 清单是后端声明的唯一来源，内置表只保证清单不可用时仍能采集。
   */
  const fieldDefinitions = () => manifestFieldDefinitions() || FIELD_DEFINITIONS;
  let indexedDefinitions: Record<string, FieldDefinition> | null = null;
  let labelIndex = new Map<string, Array<{ field: string; definition: FieldDefinition }>>();
  const definitionsByLabel = () => {
    const definitions = fieldDefinitions();
    if (definitions !== indexedDefinitions) {
      indexedDefinitions = definitions;
      labelIndex = new Map();
      for (const [field, definition] of Object.entries(definitions)) {
        for (const label of new Set(definition.aliases.map(normalizeText))) {
          const entries = labelIndex.get(label) || [];
          entries.push({ field, definition });
          labelIndex.set(label, entries);
        }
      }
    }
    return labelIndex;
  };


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
  const WRITABLE_TARGETS: Record<string, string> = {};
  const IGNORED_CONTROL_LABELS = new Set(["报废车挂靠", "新车挂靠"]);
  const SYSTEM_CONTROL_LABELS = new Set(["审核状态", "审核进度", "审核结果"]);

  const normalizeText = (value: unknown) => String(value || "")
    .replace(/[＊*]/g, "")
    .replace(/\s+/g, "")
    .replace(/[：:]$/, "");
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

  const definitionForLabel = (label: string) => {
    const normalized = normalizeText(label);
    const matches = definitionsByLabel().get(normalized) || [];
    if (matches.length !== 1) return null;
    const { field, definition } = matches[0];
    return { field, ...definition };
  };

  const isKnownLabel = (label: string) => {
    const normalized = normalizeText(label);
    return definitionsByLabel().has(normalized);
  };

  const writableTargetField = (label: string) => {
    const normalized = normalizeText(label);
    const match = Object.entries(WRITABLE_TARGETS).find(([targetLabel]) =>
      normalized === targetLabel || normalized.startsWith(targetLabel),
    );
    return match?.[1] || null;
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

  const isSectionRequired = (field: string) => Boolean(fieldDefinitions()[field]?.sectionRequired);

  const queryAll = (root: DomRoot | null | undefined, selector: string) => {
    try {
      return Array.from(root?.querySelectorAll?.<DomElement>(selector) || []);
    } catch {
      return [];
    }
  };

  const readControlValue = (control: DomElement | null | undefined) => {
    if (!control) return "";
    if (control?.tagName === "SELECT") {
      return String(control.selectedOptions?.[0]?.textContent || control.value || "").trim();
    }
    // Ant Design/Element 的 combobox value 可能是字典 code（例如
    // ENTERPRISE/NATURAL_GAS），页面上真正展示的是 option label。优先读取
    // 当前可见 label，避免把内部 code 当成页面原值与材料中文值比较。
    const role = String(control?.getAttribute?.("role") || "").toLowerCase();
    if (role === "combobox" || control?.getAttribute?.("aria-controls") || control?.getAttribute?.("aria-owns")) {
      const item = control.closest?.<DomElement>(FORM_ITEM_SELECTOR);
      const selected = visibleText(item?.querySelector?.<DomElement>(
        ".ant-select-selection-item, .el-select__selected-item, .el-input__inner",
      ));
      if (isUsableValue(selected)) return selected;
    }
    return String(control?.value ?? control?.textContent ?? "").trim();
  };

  const findSection = (element: DomElement | null | undefined) => {
    const fixtureSection = element?.dataset?.reviewSection;
    if (fixtureSection && fixtureSection !== "unknown") return fixtureSection;

    // Stop before the document body: broad container text can mention both old
    // and new vehicles and would incorrectly assign repeated labels.
    let current = element;
    for (let level = 0; current && level < 8; level += 1, current = current.parentElement) {
      if (["BODY", "HTML"].includes(current.tagName)) break;
      const ownSection = sectionFromText(
        `${directText(current)} ${current.getAttribute?.("aria-label") || ""}`,
      );
      if (ownSection !== "unknown") return ownSection;

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
    if (control.id && root?.querySelectorAll) {
      const escapedId = globalThis.CSS?.escape ? globalThis.CSS.escape(control.id) : control.id;
      queryAll(root, `label[for="${escapedId}"]`).forEach((label) => labels.push(visibleText(label)));
    }
    const formItem = control.closest?.<DomElement>(FORM_ITEM_SELECTOR);
    const wrappingLabel = control.closest?.<DomElement>("label");
    const itemLabel = formItem?.querySelector?.<DomElement>(
      ".ant-form-item-label label, .el-form-item__label, label",
    );
    labels.push(
      visibleText(itemLabel),
      visibleText(wrappingLabel),
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
        writableTargetField(candidate.label) ||
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
    const role = String(control?.getAttribute?.("role") || "").toLowerCase();
    const customEditable = role === "combobox" || control?.getAttribute?.("contenteditable") === "true";
    return customEditable || (control?.readOnly !== true && control?.getAttribute?.("readonly") == null);
  };

  const controlType = (control: DomElement | null | undefined) => {
    const role = String(control?.getAttribute?.("role") || "").toLowerCase();
    if (role === "combobox" || control?.tagName === "SELECT") return "select";
    if (control?.tagName === "TEXTAREA") return "textarea";
    if (control?.getAttribute?.("contenteditable") === "true") return "contenteditable";
    return String(control?.getAttribute?.("type") || "text").toLowerCase() || "text";
  };

  const displayLabel = (label: string) => String(label || "")
    .replace(/[＊*]/g, "")
    .replace(/[：:]\s*$/, "")
    .replace(/\s+/g, " ")
    .trim();

  const readInventoryValue = (control: DomElement | null | undefined) => {
    if (!control) return "";
    const direct = readControlValue(control);
    if (isUsableValue(direct)) return direct;
    const container = control.closest?.<DomElement>(FORM_ITEM_SELECTOR);
    const selected = visibleText(container?.querySelector?.<DomElement>(
      ".ant-select-selection-item, .el-select__selected-item, .el-input__inner",
    ));
    return isUsableValue(selected) ? selected : "";
  };

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

    if (children.length === 2 && (isKnownLabel(visibleText(children[0])) || writableTargetField(visibleText(children[0])))) {
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
      .filter((candidate) => candidate.label && (isUsableValue(candidate.value) || writableTargetField(candidate.label)));

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
      if (!isKnownLabel(ownText) && !writableTargetField(ownText)) continue;
      if (queryAll(element.nextElementSibling, CONTROL_SELECTOR).some(isDataControl)) continue;
      const value = visibleText(isDomElement(element.nextElementSibling) ? element.nextElementSibling : null);
      if ((!isUsableValue(value) && !writableTargetField(ownText)) || value.length > 200) continue;
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

  const matchingScore = (_field: string, definition: FieldDefinition, candidate: FieldCandidate, scopedControls: DomElement[] = []) => {
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

  const resolveInventoryField = (candidate: FieldCandidate, businessType: string | null, scopedControls: DomElement[]) => {
    void businessType;
    const ranked = Object.entries(fieldDefinitions())
      .map(([field, definition]) => ({
        field,
        definition,
        score: matchingScore(
          field,
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

  const reviewFieldInventory = (root: DomRoot | null | undefined, controls: DomElement[], businessType: string | null) => {
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
      const operationField = writableTargetField(label);
      const operationLabel = operationField
        ? Object.entries(WRITABLE_TARGETS).find(([, field]) => field === operationField)?.[0]
        : null;
      const resolved = operationField ? null : resolveInventoryField(candidate, businessType, controls);
      if (resolved?.definition.reviewable === false) continue;
      // 隐藏控件不是当前详情字段；只读的已知字段（日期等）仍需进入审核目录，
      // 但会以 editable=false 传给后端，避免提供页面回写按钮。
      if (!isEditableControl(control) && !resolved && !operationField) continue;
      inventory.push({
        field: operationField || resolved?.field || null,
        label: operationLabel || resolved?.definition.aliases[0] || displayLabel(label),
        value: normalizeCollectedFieldValue(operationField || resolved?.field, candidate.value),
        controlType: controlType(control),
        editable: isEditableControl(control),
        section: candidate.section,
        operationOnly: Boolean(operationField),
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

  const collectCandidates = (candidates: FieldCandidate[], scannedControls = 0, businessType: string | null = null, reviewFields: ReviewFieldSnapshot[] = [], scopedControls: DomElement[] = [], onlyFields?: ReadonlySet<string>) => {
    void businessType;
    const pageFields: Record<string, string> = {};
    const fieldTargets = [];
    const unmatchedLabels = [];
    const ambiguousFields = [];
    const writableTargets = (onlyFields ? [] : Object.entries(WRITABLE_TARGETS)).flatMap(([label, field]) => {
      // 页面有时会把表单项标签和控件占位文本拼接成“报废车挂靠 报废车挂靠”。
      // 与 writableTargetField 使用同一套前缀匹配，避免控件已识别但自动填写目标丢失。
      const targets = candidates.filter((candidate) => writableTargetField(candidate.label) === field);
      if (targets.length !== 1) {
        if (targets.length > 1) ambiguousFields.push(field);
        return [];
      }
      const [target] = targets;
      if (target.element) fieldTargets.push({ field, element: target.element });
      const currentValue = String(target.value || "").trim() || null;
      return [{ field, label, present: true, currentValue }];
    });
    // reviewFieldInventory is built from the same logical form item and is a
    // fallback when the label/value candidate is represented by a custom
    // control that does not expose a normal candidate value.
    for (const target of reviewFields.filter((item) => item.operationOnly && item.field)) {
      if (!target.field) continue;
      if (!writableTargets.some((item) => item.field === target.field)) {
        writableTargets.push({
          field: target.field,
          label: Object.entries(WRITABLE_TARGETS).find(([, field]) => field === target.field)?.[0] || target.label,
          present: true,
          currentValue: String(target.value || "").trim() || null,
        });
      }
    }

    for (const [field, definition] of Object.entries(fieldDefinitions())) {
      if (onlyFields && !onlyFields.has(field)) continue;
      const ranked = candidates
        .map((candidate) => ({
          candidate,
          score: matchingScore(field, definition, candidate, scopedControls),
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

    if ((!onlyFields || onlyFields.has("invoice.invoice_date")) && !pageFields["invoice.invoice_date"]) {
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
      writableTargets,
      unmatchedLabels,
      ambiguousFields,
      candidateCount: candidates.length,
      scannedControls,
      reviewFields,
    };
  };

  const collect = (root: DomRoot | null | undefined, businessType: string | null = null, onlyFields?: ReadonlySet<string>) => {
    const scopedRoot = reviewRoot(root);
    const rawControls = queryAll(scopedRoot, CONTROL_SELECTOR)
      .filter((control) => isDataControl(control) && isVisibleControl(control) && !isSearchFilterControl(control));
    const controls = controlCandidates(scopedRoot, rawControls);
    const reviewFields = onlyFields ? [] : reviewFieldInventory(scopedRoot, rawControls, businessType);
    const inSearchFilter = (candidate: FieldCandidate) => isSearchFilterControl(candidate.element);
    return collectCandidates(
      [
        ...controls,
        ...structuredCandidates(scopedRoot),
        ...adjacentCandidates(scopedRoot),
      ].filter((candidate) => !inSearchFilter(candidate) && !isSystemControlLabel(candidate.label) && !isIgnoredControlLabel(candidate.label) && !isVerificationCandidate(candidate)),
      rawControls.length,
      businessType,
      reviewFields,
      rawControls,
      onlyFields,
    );
  };

  export const ReviewPageFieldCollector = {
    collect,
    // 写回守卫只需要身份字段；仍实时解析 DOM 并保留重复目标的歧义检查。
    collectIdentity: (root: DomRoot, fields: readonly string[]) => collect(root, null, new Set(fields)),
    collectCandidates,
    definitionForLabel,
    isSectionRequired,
    sectionFromText,
  };

import { manifestWritableFields } from "../catalog/collect-manifest.ts";
import type { DomElement, DomRoot } from "../dom.ts";
import { antDatePicker, formControlKind, isSelectControl, logicalFormControl, readFormControlValue } from "../form-controls.ts";
import { calendarDate, selectCalendarDate } from "./date-picker.ts";
import type { PageWriteAction, PageWriteGroupAction } from "../../../shared/contracts/review.ts";
import type { InspectExactOption } from "./exact-select.ts";
import { SelectInspectionError } from "./exact-select.ts";
type SequenceWriteValue = PageWriteAction & {label: string};
export interface WriteObservation {
  wait: () => Promise<{ok: boolean; code?: string; message?: string}>;
  dispose: () => void;
}
interface SequenceWritePolicy extends WritePolicy {
  label?: string;
  focusField?: string;
  observeWrite?: (item: SequenceWriteValue, control: DomElement) => WriteObservation | undefined;
  useLiveOptionLabels?: boolean;
  optionValueFields?: Readonly<Record<string, readonly string[]>>;
}
type Guard = (() => boolean) | undefined;
const activeWrites = new WeakSet<DomRoot>();

async function withPageWrite<T>(root: DomRoot, operation: () => Promise<T>) {
  if (activeWrites.has(root)) return { ok: false as const, code: "WRITE_IN_PROGRESS",
    message: "页面正在执行其他回填，请稍后重试", currentValue: undefined, actions: [] };
  activeWrites.add(root);
  try { return await operation(); } finally { activeWrites.delete(root); }
}
interface PopupControl { control: DomElement; trigger?: DomElement | null; listboxId?: string | null; }
type GroupPrepared = {
  field: string;
  control: DomElement;
  custom: boolean;
  original: string;
  resolved: PopupControl;
};
type GroupPreparationFailure = {
  error: string;
  code?: string;
  currentValue?: string;
};
/** Controlled field writes use the current collection and business allowlist. */

// The production page currently renders Ant Design `a-form-item`, but the
// same form is deployed in a few shells where the wrapper class is changed
// (or removed by a micro-frontend).  Keep the framework selectors first and
// use the data/class fallbacks only when resolving a labelled control.
const ITEM_SELECTOR = ".ant-form-item, .el-form-item, .form-item, [data-form-item], [data-index][class*='form'], [class*='FormItem'], [class*='formItem']";
const LABEL_SELECTOR = ".ant-form-item-label label, .el-form-item__label, label, [data-label], [data-field-label]";
const COMBOBOX_SELECTOR = "[role='combobox'], input[aria-controls], input[aria-owns]";
// 兼容 Ant Design、Element 及无 role 的自定义列表；文本节点可能包在子节点内。
const OPTION_SELECTOR = ".ant-select-item-option, .ant-select-dropdown-menu-item, .el-select-dropdown__item, [role='option'], li[data-value], li[data-label], [data-value], [data-label]";
const normalize = (value: unknown) => String(value || "").replace(/[＊*]/g, "").replace(/\s+/g, "").replace(/[：:]$/, "");
const normalizeAmount = (value: unknown) => {
  const raw = String(value ?? "").trim().replace(/[￥¥,，\s]/g, "");
  if (!raw) return "";
  const match = raw.match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if (!match) return raw;
  const integer = (match[2] || "0").replace(/^0+(?=\d)/, "");
  const fraction = (match[3] || "").replace(/0+$/, "");
  const sign = match[1] === "-" && (integer !== "0" || fraction) ? "-" : "";
  return `${sign}${integer}${fraction ? `.${fraction}` : ""}`;
};
const normalizeFieldValue = (field: string, value: unknown) =>
  field === "invoice.amount" ? normalizeAmount(value) : normalize(value);
// `innerText` is an empty string for virtualized/transitioning Ant options
// even though `textContent` already contains the label.  Nullish fallback
// is therefore insufficient; choose the first non-empty representation.
const text = (element: DomElement | null | undefined) => [
  element?.innerText,
  element?.textContent,
  element?.getAttribute?.("aria-label"),
  element?.getAttribute?.("data-label"),
  element?.getAttribute?.("title"),
  element?.value,
].map((value) => String(value ?? "").trim()).find(Boolean) || "";
const queryAll = (root: DomRoot | null | undefined, selector: string) => Array.from(root?.querySelectorAll?.<DomElement>(selector) || []);
const connected = (element: DomElement | null | undefined) => element?.isConnected !== false;
const hidden = (element: DomElement | null | undefined) => {
  for (let node = element; node; node = node.parentElement) {
    if (node.hidden === true || node.getAttribute?.("aria-hidden") === "true" || node.style?.display === "none" || node.style?.visibility === "hidden") return true;
    const getComputedStyle = node.ownerDocument?.defaultView?.getComputedStyle;
    if (typeof getComputedStyle === "function") {
      const style = getComputedStyle.call(node.ownerDocument.defaultView, node);
      if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse") return true;
    }
  }
  return false;
};
const disabled = (element: DomElement | null | undefined) => {
  for (let node = element; node; node = node.parentElement) {
    if (node.disabled === true || node.getAttribute?.("aria-disabled") === "true"
      || node.classList?.contains("ant-picker-disabled") || node.classList?.contains("ant-select-disabled")) return true;
  }
  return false;
};
const writable = (element: DomElement | null | undefined, custom = false) => connected(element) && !hidden(element) && !disabled(element) && (custom || Boolean(antDatePicker(element)) || (element?.readOnly !== true && element?.getAttribute?.("readonly") == null));

// 生产页的列表详情和审核弹窗可能同时保留两份表单。只在当前可见的
// 详情弹窗内定位输入控件，避免后台列表/预览副本把目标判成“重复”。
// 下拉选项仍从 document 根节点扫描，因为 Ant Design 会把 dropdown
// portal 挂到 body，而不是挂在表单弹窗内。
const activeFormRoot = (root: DomRoot) => {
  const candidates = queryAll(root, ".my-page-modal, .ant-modal, .ant-drawer, [role='dialog']")
    .filter((item) => !hidden(item))
    .filter((item) => item.querySelector?.<DomElement>(".largeForm, .ant-form, .el-form, form"));
  const current = candidates.at(-1);
  const selected = current?.querySelector?.<DomElement>(".largeForm, .ant-form, .el-form, form") || current;
  if (selected) return selected;
  // The production drawer sometimes has no dialog class at all. Prefer the
  // last visible largeForm before falling back to document root.
  const forms = queryAll(root, ".largeForm, .ant-form, .el-form, form").filter((item) => !hidden(item));
  return forms.at(-1) || root;
};

const nearestFieldItem = (element: DomElement | null | undefined) => {
  if (!element) return null;
  if (typeof element.closest === "function") {
    const item = element.closest(ITEM_SELECTOR);
    if (item) return item;
  }
  // Last-resort fallback for a wrapper without a framework class.  Stop at
  // the first ancestor that owns the label and exactly one logical control;
  // never return the document/body, which would make both repeated fields
  // appear as one ambiguous item.
  for (let node = element.parentElement; node && node.parentElement; node = node.parentElement) {
    const labels = queryAll(node, LABEL_SELECTOR);
    const controls = queryAll(node, `input:not([type='hidden']), textarea, select, ${COMBOBOX_SELECTOR}`);
    if (labels.length === 1 && controls.length === 1) return node;
    if (node.tagName === "FORM" || node.tagName === "FIELDSET") break;
  }
  return null;
};

function customTrigger(control: DomElement) {
  return control.closest?.<DomElement>(".ant-select")?.querySelector?.<DomElement>(".ant-select-selector")
    || control.closest?.<DomElement>(".el-select")?.querySelector?.<DomElement>(".el-input__wrapper, .el-input")
    || control;
}

function toggleCustomControl(resolved: PopupControl) {
  const trigger = resolved.trigger || resolved.control;
  // Ant Design opens on mousedown in some versions, while Element opens on
  // click. Dispatch both when available; the native click remains the
  // fallback used by the lightweight test fixture.
  const EventClass = trigger?.ownerDocument?.defaultView?.MouseEvent || globalThis.MouseEvent;
  const eventInit = { bubbles: true, cancelable: true, button: 0, buttons: 1, view: trigger?.ownerDocument?.defaultView || null };
  if (EventClass) trigger?.dispatchEvent?.(new EventClass("mousedown", eventInit));
  trigger?.click?.();
}

function triggerInvoiceVerification(root: DomRoot, target: DomElement, expectedValue: string) {
  if (activeWrites.has(root)) return { ok: false, message: "页面正在回填，请完成后再核验" };
  const snapshot = captureValue(target);
  if (!snapshot || hidden(target) || !connected(target) || !normalize(expectedValue)
    || normalize(snapshot.value) !== normalize(expectedValue)) {
    return { ok: false, message: "发票号码已变化或未采集，请重新审核后验真" };
  }
  const formRoot = activeFormRoot(root);
  const item = nearestFieldItem(target);
  if (!item || (typeof formRoot.contains === "function" && !formRoot.contains(item))) {
    return { ok: false, message: "发票号码控件已变化，请重新采集" };
  }
  const candidates = queryAll(item, "button, input[type='button'], input[type='submit'], [role='button'], a");
  const isVerifyButton = (candidate: DomElement) => /一键验真/.test(`${text(candidate)} ${candidate.value || ""}`)
    && writable(candidate, true);
  const buttons = candidates.filter(isVerifyButton);
  if (buttons.length !== 1) return { ok: false, message: buttons.length ? "发票号码旁存在重复验真按钮，已停止执行" : "未找到发票号码旁的一键验真按钮" };
  const button = buttons[0];
  button.click?.();
  return { ok: true, message: "已点击一键验真" };
}

const settle = () => globalThis.setTimeout
  ? new Promise<void>((resolve) => globalThis.setTimeout(resolve, 50))
  : Promise.resolve();

const guardError = "页面已变化，请重新审核";
const sameCollectedRecord = (guard: Guard) => typeof guard !== "function" || guard();

function dispatchNative(select: DomElement, root: DomRoot) {
  const EventClass = root?.defaultView?.Event || globalThis.Event;
  if (!EventClass) return;
  select.dispatchEvent?.(new EventClass("input", { bubbles: true }));
  select.dispatchEvent?.(new EventClass("change", { bubbles: true }));
}

async function selectCustomOption(root: DomRoot, resolved: PopupControl, targetLabel: string, label: string, guard: Guard, optionValue?: string, policy?: WritePolicy) {
  if (!resolved.listboxId) return `${targetLabel}未关联选项列表，请手工填写`;
  if (optionValue && !policy?.inspectOption) return `${targetLabel}无法核验真实选项 ID，已停止回填`;
  if (resolved.control.getAttribute?.("aria-expanded") !== "true") toggleCustomControl(resolved);
  // 清除原页面遗留的搜索词，让目标 ID 出现在实际过滤列表中。
  if (optionValue && resolved.control.tagName === "INPUT" && resolved.control.value && !resolved.control.readOnly) {
    dispatchValue(resolved.control, "", root);
  }
  let target = null;
  for (let attempt = 0; attempt < (optionValue ? 100 : 20) && !target; attempt += 1) {
    await settle();
    if (!sameCollectedRecord(guard)) return guardError;
    if (optionValue && policy?.inspectOption) {
      const snapshot = await policy.inspectOption(resolved.control, {id: optionValue, label, prepare: true});
      if (!sameCollectedRecord(guard) || !connected(resolved.control)) return guardError;
      const exact = snapshot.optionToken ? queryAll(root, "[data-review-option-token]")
        .filter(item => item.getAttribute("data-review-option-token") === snapshot.optionToken) : [];
      if (exact.length === 1 && writable(exact[0]) && !exact[0]!.classList?.contains("ant-select-item-option-disabled")
        && normalize(text(exact[0])) === normalize(label)) {
        target = exact[0];
        break;
      }
      // 只按真实 ID 等待虚拟列表渲染，不能退回名称匹配。
      continue;
    }
    // Ant's aria-controls points to a zero-sized accessibility list. Its
    // actual clickable options are siblings inside the same owned dropdown.
    // Never widen this search to other dropdowns in the document.
    const ids = [resolved.control.getAttribute?.("aria-controls"), resolved.control.getAttribute?.("aria-owns"), resolved.listboxId]
      .filter((id): id is string => Boolean(id)).flatMap(id => id.split(/\s+/));
    const listbox = ids.map(id => root.getElementById?.(id)).find(Boolean);
    const dropdown = listbox?.closest?.<DomElement>(".ant-select-dropdown, .el-select-dropdown");
    const scope = dropdown || listbox;
    const visualOptions = queryAll(scope, ".ant-select-item-option, .ant-select-dropdown-menu-item, .el-select-dropdown__item");
    const options = (scope && connected(scope) && !hidden(scope)
      ? (visualOptions.length ? visualOptions : queryAll(scope, OPTION_SELECTOR)) : [])
      .map((item) => item.closest?.<DomElement>(OPTION_SELECTOR) || item)
      .filter((item, index, all) => all.indexOf(item) === index)
      .filter((item) => writable(item) && !item.classList?.contains("ant-select-item-option-disabled")
        && !item.classList?.contains("is-disabled") && normalize(text(item)) === normalize(label))
      .filter(item => !optionValue || !item.getAttribute?.("data-value") || item.getAttribute("data-value") === optionValue);
    if (options.length > 1) {
      toggleCustomControl(resolved);
      return `${targetLabel}选项存在歧义，请手工填写`;
    }
    target = options[0] || null;
  }
  if (!target) {
    toggleCustomControl(resolved);
    await settle();
    return `${targetLabel}未找到唯一可用选项，请手工填写`;
  }
  if (!sameCollectedRecord(guard)) return guardError;
  target.click?.();
  target.removeAttribute?.("data-review-option-token");
  return null;
}

function writableValueControl(entry: DomElement | null | undefined) {
  if (!entry) return null;
  if (isSelectControl(entry)) return logicalFormControl(entry);
  if (entry.getAttribute?.("role") === "combobox") return entry;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(entry.tagName)) return entry;
  const candidates = queryAll(entry, "[role='combobox'], input, textarea, select")
    .filter((control) => connected(control) && !hidden(control) && control.getAttribute?.("type") !== "hidden");
  return candidates.length === 1 ? candidates[0] : null;
}

function dispatchValue(control: DomElement, value: string, root: DomRoot, optionValue?: string) {
  const EventClass = root?.defaultView?.Event || globalThis.Event;
  if (control.tagName === "SELECT") {
    const options = Array.from(control.options || []).filter((option) => normalize(text(option)) === normalize(value)
      && (!optionValue || String(option.value) === optionValue));
    if (options.length !== 1) return "页面选项不存在或存在歧义";
    control.value = String(options[0].value ?? text(options[0]));
  } else {
    const prototype = control.tagName === "TEXTAREA" ? root.defaultView?.HTMLTextAreaElement?.prototype : root.defaultView?.HTMLInputElement?.prototype;
    const setter = prototype && Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (setter) setter.call(control, value); else control.value = value;
  }
  if (EventClass) {
    control.dispatchEvent?.(new EventClass("input", { bubbles: true }));
    control.dispatchEvent?.(new EventClass("change", { bubbles: true }));
  }
  return null;
}

async function writeControl(root: DomRoot, resolved: PopupControl, field: string, value: string, guard: Guard, optionValue?: string, policy?: WritePolicy) {
  if (antDatePicker(resolved.control)) {
    return selectCalendarDate(root, resolved.control, value, {
      visible: element => connected(element) && !hidden(element),
      enabled: element => connected(element) && !disabled(element),
      pause: settle,
      guard: () => sameCollectedRecord(guard),
    });
  }
  if (resolved.control.tagName !== "SELECT" && isSelectControl(resolved.control)) return selectCustomOption(root, resolved, field, value, guard, optionValue, policy);
  return dispatchValue(resolved.control, value, root, optionValue);
}

const readValueControl = readFormControlValue;

function captureValue(entry: DomElement | null | undefined) {
  const control = writableValueControl(entry);
  return control ? { control, value: readValueControl(control) } : null;
}

// 控件类型判定必须和采集器一致：采集器决定控件能不能写，这里决定写回器
// 是否支持这种控件。两处用同一套取值，业务声明才能同时约束采集和写回。
const controlKind = formControlKind;

/**
 * 写回策略：后端下发的字段与控件类型白名单。
 *
 * 没传（或没应用清单）时退回内置的报废置换白名单。`controlKinds` 空集合
 * 表示业务声明了"不限制"，与没声明是两件事。
 */
interface WritePolicy {
  fields?: ReadonlySet<string> | null;
  controlKinds?: ReadonlySet<string> | null;
  inspectOption?: InspectExactOption;
}

async function optionId(control: DomElement, policy?: WritePolicy) {
  if (control.tagName === "SELECT") return String(control.value ?? "");
  if (isSelectControl(control) && policy?.inspectOption) {
    return (await policy.inspectOption(control)).selectedValue;
  }
  return null;
}

const fieldAllowed = (field: string, policy: WritePolicy | undefined) =>
  (policy?.fields ?? manifestWritableFields())?.has(field) ?? false;

const presentationTarget = (control: DomElement) => control.closest?.<DomElement>(
  ".ant-input-affix-wrapper, .ant-input-number, .ant-select, .ant-picker, .el-input, .el-input-number, .el-select, .el-date-editor",
) || control;

function revealControl(control: DomElement) {
  const target = presentationTarget(control);
  try { target.scrollIntoView?.({ behavior: "smooth", block: "center", inline: "nearest" }); } catch { target.scrollIntoView?.(); }
  // Refocusing an Ant selector or calendar after committing can reopen its popup.
  if (!isSelectControl(control) && !antDatePicker(control)) control.focus?.({ preventScroll: true });
}

function highlightControl(control: DomElement) {
  const target = presentationTarget(control);
  target.animate?.([
    { outline: "3px solid #1677ff", outlineOffset: "3px", backgroundColor: "#e6f4ff" },
    { outline: "3px solid #1677ff", outlineOffset: "3px", backgroundColor: "#e6f4ff", offset: 0.85 },
    { outline: "3px solid transparent", outlineOffset: "3px" },
  ], { duration: 4000 });
}

async function executeValue(root: DomRoot, entry: DomElement | null | undefined, action: PageWriteAction, guard: Guard, policy?: WritePolicy) {
  if (!action || !fieldAllowed(action.field, policy)) return { ok: false, message: "该字段不在允许回填范围内" };
  if (!entry || !sameCollectedRecord(guard)) return { ok: false, message: guardError };
  const control = writableValueControl(entry);
  const custom = control?.tagName !== "SELECT" && isSelectControl(control);
  if (!control || !writable(control, custom)) return { ok: false, message: "目标字段不可写或页面已变化" };
  const kinds = policy?.controlKinds;
  if (kinds && kinds.size && !kinds.has(controlKind(control))) {
    return { ok: false, message: "该控件类型暂不支持自动回填，请手工填写" };
  }
  if (!String(action.value ?? "").trim()) return { ok: false, message: "回填值不能为空" };
  // OCR 保留票面原文；写入日期控件前统一格式，回读也比较日期语义。
  // 报废置换、车源和过户共用此路径，不改写材料证据的展示值。
  const isDate = controlKind(control) === "date";
  const fillValue = isDate ? calendarDate(action.value) : action.value;
  if (!fillValue) return { ok: false, message: "日期格式无效，请使用有效的 YYYY-MM-DD 日期" };
  const comparable = (value: string) => isDate ? calendarDate(value) ?? value : normalizeFieldValue(action.field, value);
  const original = readValueControl(control);
  const originalId = action.optionValue ? await optionId(control, policy) : undefined;
  if (!sameCollectedRecord(guard)) return {ok: false, message: guardError};
  if (action.optionValue && originalId === null) return {ok: false, message: "无法核验真实选项 ID，已停止回填"};
  const originalRaw = control.tagName === "SELECT" ? String(control.value ?? "") : original;
  if (action.expectedValue != null && comparable(original) !== comparable(action.expectedValue)) {
    return { ok: false, code: "FIELD_VALUE_CHANGED", currentValue: original,
      message: "该字段当前值与采集时不同，已更新页面值，请核对后再次回填" };
  }
  const resolved = {
    control, trigger: customTrigger(control),
    listboxId: control.getAttribute?.("aria-controls") || control.getAttribute?.("aria-owns"),
  };
  const error = await writeControl(root, resolved, action.field, fillValue, guard, action.optionValue, policy);
  if (error) return { ok: false, message: error };
  await settle();
  if (!sameCollectedRecord(guard) || !connected(control)) return { ok: false, message: guardError };
  const after = readValueControl(control);
  if (comparable(after) !== comparable(fillValue)
    || (action.optionValue && await optionId(control, policy) !== action.optionValue)) {
    if (sameCollectedRecord(guard)) {
      if (custom || antDatePicker(control)) {
        if (action.optionValue && !originalId) return {ok: false, message: "回填后真实 ID 不一致，请核对页面；原选项为空，未自动恢复"};
        const restoreError = await writeControl(root, resolved, action.field, original, guard, originalId || undefined, policy);
        if (restoreError) return { ok: false, message: `回填后回读失败；${restoreError}` };
      } else if (control.tagName === "SELECT") {
        control.value = originalRaw;
        dispatchNative(control, root);
      } else {
        dispatchValue(control, original, root);
      }
      await settle();
      if (!sameCollectedRecord(guard) || !connected(control)) return { ok: false, message: `${guardError}；请人工核对已写入字段` };
      if (normalizeFieldValue(action.field, readValueControl(control)) !== normalizeFieldValue(action.field, original)
        || (action.optionValue && await optionId(control, policy) !== originalId)) {
        return { ok: false, message: "回填后回读失败；原值恢复失败，请人工核对页面" };
      }
    }
    return { ok: false, message: "回填后回读失败" };
  }
  if (!sameCollectedRecord(guard) || !connected(control)) return {ok: false, message: guardError};
  revealControl(control);
  highlightControl(control);
  return { ok: true, message: "字段已回填并回读，已定位到页面字段", actions: [{ field: action.field, label: action.field, value: after, status: "FILLED" }] };
}

async function executeValueGroup(
  root: DomRoot,
  entries: Array<{ field: string; element: DomElement; expectedValue?: string | null }>,
  action: PageWriteGroupAction,
  guard: Guard,
  policy?: WritePolicy,
) {
  if (!action.fields.length || action.fields.length !== entries.length || !String(action.value || "").trim()
    || new Set(action.fields).size !== action.fields.length || new Set(entries.map(entry => entry.field)).size !== entries.length
    || entries.some(entry => !action.fields.includes(entry.field))) {
    return { ok: false, message: "组合字段回填参数不完整", actions: [] };
  }
  const prepared: Array<GroupPrepared | GroupPreparationFailure> = entries.map((entry) => {
    // 组合字段和单字段走同一份白名单。这里以前只认内置的报废置换表，
    // 新业务的组合字段会被误判成"不在允许范围内"——
    // 而且报错信息里写着报废置换，排查的人会以为是业务选错了。
    if (!fieldAllowed(entry.field, policy)) return { error: `${entry.field}不在本业务允许回填范围内` };
    const control = writableValueControl(entry.element);
    const custom = control?.tagName !== "SELECT" && isSelectControl(control);
    if (!control || !writable(control, custom)) return { error: `${entry.field}目标控件不可写` };
    const kinds = policy?.controlKinds;
    if (kinds && kinds.size && !kinds.has(controlKind(control))) {
      return { error: `${entry.field}的控件类型暂不支持自动回填，请手工填写` };
    }
    const current = readValueControl(control);
    const expected = entry.expectedValue ?? action.expectedValues[entry.field];
    if (expected != null && normalizeFieldValue(entry.field, current) !== normalizeFieldValue(entry.field, expected)) {
      return { error: `${entry.field}当前值已变化，请重新采集后回填`, code: "FIELD_VALUE_CHANGED", currentValue: current };
    }
    return {
      field: entry.field,
      control,
      custom,
      original: current,
      resolved: { control, trigger: customTrigger(control), listboxId: control.getAttribute?.("aria-controls") || control.getAttribute?.("aria-owns") },
    };
  });
  const failed = prepared.find((item): item is GroupPreparationFailure => "error" in item);
  if (failed) return { ok: false, message: failed.error, code: failed.code, currentValue: failed.currentValue, actions: [] };
  const successful = prepared.filter((item): item is GroupPrepared => "control" in item);
  if (new Set(successful.map(item => item.control)).size !== successful.length) {
    return { ok: false, message: "组合字段指向重复的页面控件，请重新采集", actions: [] };
  }
  const written: GroupPrepared[] = [];
  const rollback = async (message: string) => {
    const errors: string[] = [];
    if (!sameCollectedRecord(guard)) return { ok: false, message: `${message}；页面身份已失效，已停止回滚写入`, actions: [] };
    for (const item of [...written].reverse()) {
      const restoreError = await writeControl(root, item.resolved, item.field, item.original, guard);
      if (restoreError) errors.push(String(restoreError));
    }
    await settle();
    if (!sameCollectedRecord(guard)) return { ok: false, message: `${message}；页面身份已失效，请人工核对已写入字段`, actions: [] };
    for (const item of written) {
      if (!connected(item.control) || normalizeFieldValue(item.field, readValueControl(item.control)) !== normalizeFieldValue(item.field, item.original)) {
        errors.push(`${item.field}原值恢复后回读失败`);
      }
    }
    return errors.length
      ? { ok: false, message: `${message}；自动回滚未完成（${errors.join("；")}），请人工核对页面`, actions: [] }
      : { ok: false, message: `${message}；已回滚`, actions: [] };
  };
  for (const item of successful) {
    if (!sameCollectedRecord(guard)) return await rollback("页面身份已失效，已停止组合回填");
    written.push(item);
    const error = await writeControl(root, item.resolved, item.field, action.value, guard);
    if (error) {
      // 日历可能已提交日期但在关闭弹层时失败；该控件也必须进入回滚。
      if (readValueControl(item.control) === item.original) written.pop();
      return await rollback(String(error));
    }
    await settle();
  }
  if (!sameCollectedRecord(guard)) return await rollback("页面身份已失效，已停止组合回填");
  const results = [];
  for (const item of successful) {
    const after = readValueControl(item.control);
    if (normalizeFieldValue(item.field, after) !== normalizeFieldValue(item.field, action.value)) return await rollback(`${item.field}回填后回读失败`);
    results.push({ field: item.field, label: item.field, value: after, status: "FILLED" });
  }
  // Reveal once, after every readback succeeds, so multiple smooth scrolls do not compete.
  revealControl(successful[0].control);
  for (const item of successful) highlightControl(item.control);
  return { ok: true, message: "组合字段已同时回填并回读，已定位到页面字段", actions: results };
}

/** Read a cached current identity before requiring options for a new selection. */
async function resolveSequenceOptionLabel(root: DomRoot, control: DomElement, item: SequenceWriteValue,
  guard: Guard, policy: SequenceWritePolicy): Promise<string> {
  const inspect = policy.inspectOption;
  if (!inspect) throw new SelectInspectionError("SELECT_INSPECTION_FAILED", "无法核验真实选项 ID");
  const check = (snapshot: Awaited<ReturnType<InspectExactOption>>) => {
    if (!sameCollectedRecord(guard) || !connected(control)) throw new SelectInspectionError("PAGE_IDENTITY_CHANGED", guardError);
    const allowed = policy.optionValueFields?.[item.field];
    if (allowed && !allowed.includes(snapshot.valueField || "")) {
      throw new SelectInspectionError("SELECT_VALUE_FIELD_CHANGED", "真实 ID 字段语义已变化，已停止回填");
    }
    return snapshot;
  };
  const selected = check(await inspect(control));
  if (selected.selectedValue === item.optionValue && selected.selectedLabel
    && normalize(selected.selectedLabel) === normalize(readValueControl(control))) return selected.selectedLabel;
  const resolved = {control, trigger: customTrigger(control),
    listboxId: control.getAttribute("aria-controls") || control.getAttribute("aria-owns")};
  let opened = false;
  try {
    for (let attempt = 0; ; attempt++) {
      check(selected);
      try {
        const option = check(await inspect(control, {id: item.optionValue!, label: item.value, prepare: false, resolveLabel: true}));
        if (!option.optionLabel) throw new SelectInspectionError("SELECT_LABEL_MISSING", "无法读取真实选项名称");
        return option.optionLabel;
      } catch (error) {
        // Parent linkage and lazy loading can leave options temporarily empty. Only missing IDs
        // may wait; ambiguity, disabled options, changed field semantics and identity fail at once.
        if (!(error instanceof SelectInspectionError) || error.code !== "SELECT_OPTION_MISSING" || attempt >= 99) throw error;
        if (attempt === 0) {
          if (control.getAttribute("aria-expanded") !== "true") { toggleCustomControl(resolved); opened = true; }
          if (control.tagName === "INPUT" && control.value && !control.readOnly) dispatchValue(control, "", root);
        }
        await settle();
      }
    }
  } catch (error) {
    if (opened && sameCollectedRecord(guard) && connected(control) && control.getAttribute("aria-expanded") === "true") toggleCustomControl(resolved);
    throw error;
  }
}

/** 不同值的依赖序列：父级变更后重新定位子控件，最终逐项读回。 */
async function executeSequence(
  root: DomRoot, values: SequenceWriteValue[],
  resolve: (field: string) => DomElement | null | undefined,
  guard: Guard, policy?: SequenceWritePolicy,
) {
  const label = policy?.label ?? "字段组";
  const actions: Array<{field: string; label: string; value: string; status: string}> = [];
  let optionsMayLoad = false;
  if (!values.length || new Set(values.map(item => item.field)).size !== values.length
    || values.some(item => !fieldAllowed(item.field, policy) || !String(item.value ?? "").trim())) {
    return {ok: false, message: `${label}回填范围或值无效，请重新采集`, actions};
  }
  for (const item of values) {
    if (!sameCollectedRecord(guard)) return { ok: false, code: "PAGE_IDENTITY_CHANGED", message: guardError, actions };
    const element = resolve(item.field);
    if (!element) return { ok: false, message: `${item.label}控件不存在或存在歧义，${label}未完整回填`, actions };
    let snapshot = captureValue(element);
    // Resolve aliases by the exact target ID on the current child control after parent linkage.
    if (item.optionValue && snapshot && policy?.useLiveOptionLabels) {
      let control = snapshot.control;
      if (control.tagName === "SELECT") {
        for (let attempt = 0; ; attempt++) {
          const options = Array.from(control.options || []).filter(option => String(option.value) === item.optionValue);
          if (options.length === 1 && !options[0]!.disabled) { item.value = text(options[0]); break; }
          if (options.length || !optionsMayLoad || attempt >= 99) return {ok: false,
            code: options.length > 1 ? "SELECT_OPTION_DUPLICATE" : options.length ? "SELECT_OPTION_DISABLED" : "SELECT_OPTION_MISSING",
            message: `${item.label}原页面不存在唯一可用 ID（目标 ${item.optionValue}）`, actions};
          await settle();
          if (!sameCollectedRecord(guard)) return {ok: false, code: "PAGE_IDENTITY_CHANGED", message: guardError, actions};
          snapshot = captureValue(resolve(item.field));
          if (!snapshot || snapshot.control.tagName !== "SELECT") return {ok: false, message: `${item.label}控件已变化，请重新采集`, actions};
          control = snapshot.control;
        }
      } else {
        try {
          item.value = await resolveSequenceOptionLabel(root, control, item, guard, policy);
        } catch (error) {
          return {ok: false, code: error instanceof SelectInspectionError ? error.code : "SELECT_INSPECTION_FAILED",
            message: `${item.label}回填预检失败：${error instanceof Error ? error.message : "无法核验选项"}；请核对页面，已停止后续写入`, actions};
        }
      }
      if (!sameCollectedRecord(guard) || !connected(control)) return {ok: false, message: guardError, actions};
    }
    if (snapshot && normalize(snapshot.value) === normalize(item.value)
      && (!item.optionValue || await optionId(snapshot.control, policy) === item.optionValue)) {
      if (!sameCollectedRecord(guard)) return {ok: false, message: guardError, actions};
      actions.push({field: item.field, label: item.label, value: snapshot.value, status: "UNCHANGED"});
      continue;
    }
    const target = resolve(item.field);
    const control = target && writableValueControl(target);
    const observation = control && policy?.observeWrite?.(item, control);
    let result: Awaited<ReturnType<typeof executeValue>>;
    try {
      result = await executeValue(root, target, {field: item.field, value: item.value, optionValue: item.optionValue}, guard, policy);
      if (result.ok && observation) {
        const ready = await observation.wait();
        if (!ready.ok) return {...ready, message: `${item.label}联动未完成：${ready.message}；请核对已写入字段`, actions: [...actions, ...(result.actions || [])]};
      }
    } finally { observation?.dispose(); }
    if (!result.ok) return {...result, message: `${item.label}回填失败：${result.message}；请核对已写入字段`, actions};
    actions.push(...(result.actions || []).map(action => ({...action, label: item.label})));
    if (item.optionValue) optionsMayLoad = true;
    if (!sameCollectedRecord(guard)) return {ok: false, code: "PAGE_IDENTITY_CHANGED", message: guardError, actions};
  }
  const finalControls: DomElement[] = [];
  for (let pass = 0; pass < 20; pass++) {
    await settle();
    if (!sameCollectedRecord(guard)) return { ok: false, code: "PAGE_IDENTITY_CHANGED", message: guardError, actions };
    for (const item of values) {
      const snapshot = captureValue(resolve(item.field));
      if (!snapshot || normalize(snapshot.value) !== normalize(item.value)
        || (pass === 19 && item.optionValue && await optionId(snapshot.control, policy) !== item.optionValue)) {
        return { ok: false, message: `${item.label}最终回读不一致，${label}未完整回填，请核对页面`, actions };
      }
      if (pass === 19) finalControls.push(snapshot.control);
    }
  }
  if (!sameCollectedRecord(guard)) return {ok: false, message: guardError, actions};
  if (finalControls.some(control => !connected(control))) return {ok: false, message: `${label}控件已变化，请重新采集并核对页面`, actions};
  // 联动可能重建控件；定位调用方指定的字段，高亮最终控件。
  const focusIndex = values.findIndex(item => item.field === policy?.focusField);
  revealControl(finalControls[focusIndex < 0 ? 0 : focusIndex]!);
  for (const control of finalControls) highlightControl(control);
  return { ok: true, message: `${label}已整块回填并逐项回读`, actions };
}

export const ReviewPageFieldWriter = {
  executeValue: (...args: Parameters<typeof executeValue>) => withPageWrite(args[0], () => executeValue(...args)),
  executeValueGroup: (...args: Parameters<typeof executeValueGroup>) => withPageWrite(args[0], () => executeValueGroup(...args)),
  executeSequence: (...args: Parameters<typeof executeSequence>) => withPageWrite(args[0], () => executeSequence(...args)),
  captureValue, triggerInvoiceVerification, controlKind,
};

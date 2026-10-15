import type { DomElement, DomRoot } from "./dom.ts";
import type { PageWriteAction, PageWriteGroupAction } from "../types/review.ts";
type Guard = (() => boolean) | undefined;
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

  const ALLOWED_VALUE_FIELDS = Object.freeze(new Set([
    "old_vehicle.recycle_date", "scrap_certificate.certificate_no", "old_vehicle.vin", "old_vehicle.plate_no", "old_vehicle.owner", "old_vehicle.engine_model",
    "invoice.code", "invoice.invoice_no", "invoice.amount", "invoice.invoice_date", "new_vehicle.vin", "new_vehicle.plate_no", "new_vehicle.owner",
    "page_ocr.new_vehicle_vin", "application.customer_name",
    "old_vehicle.type", "new_vehicle.fuel_type", "new_vehicle.registration_date",
    "application.terminal_phone", "application.terminal_certificate_no", "application.owner_type",
    "old_vehicle.affiliation", "new_vehicle.affiliation",
  ]));
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
      if (node.disabled === true || node.getAttribute?.("aria-disabled") === "true") return true;
    }
    return false;
  };
  const writable = (element: DomElement | null | undefined, custom = false) => connected(element) && !hidden(element) && !disabled(element) && (custom || (element?.readOnly !== true && element?.getAttribute?.("readonly") == null));

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

  function nativeCurrent(select: DomElement) {
    const value = String(select.value ?? "");
    if (!value) return { value: "", label: "" };
    const selected = Array.from(select.options || []).find((option) => String(option.value ?? text(option)) === value);
    return { value, label: text(selected) || value };
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

  async function selectCustomOption(root: DomRoot, resolved: PopupControl, targetLabel: string, label: string, guard: Guard) {
    if (!resolved.listboxId) return `${targetLabel}未关联选项列表，请手工填写`;
    toggleCustomControl(resolved);
    let target = null;
    for (let attempt = 0; attempt < 3 && !target; attempt += 1) {
      await settle();
      if (!sameCollectedRecord(guard)) return guardError;
      const listbox = root?.getElementById?.(resolved.listboxId || "");
      const options = (listbox && connected(listbox) && !hidden(listbox) ? queryAll(listbox, OPTION_SELECTOR) : [])
        .map((item) => item.closest?.<DomElement>(OPTION_SELECTOR) || item)
        .filter((item, index, all) => all.indexOf(item) === index)
        .filter((item) => writable(item) && normalize(text(item)) === normalize(label));
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
    target.click?.();
    return null;
  }

  function writableValueControl(entry: DomElement | null | undefined) {
    if (!entry) return null;
    if (entry.getAttribute?.("role") === "combobox") return entry;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(entry.tagName)) return entry;
    const candidates = queryAll(entry, "[role='combobox'], input, textarea, select")
      .filter((control) => connected(control) && !hidden(control) && control.getAttribute?.("type") !== "hidden");
    return candidates.length === 1 ? candidates[0] : null;
  }

  function dispatchValue(control: DomElement, value: string, root: DomRoot) {
    const EventClass = root?.defaultView?.Event || globalThis.Event;
    if (control.tagName === "SELECT") {
      const options = Array.from(control.options || []).filter((option) => normalize(text(option)) === normalize(value));
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

  function readValueControl(control: DomElement) {
    if (control.tagName === "SELECT") return nativeCurrent(control).label;
    if (control.getAttribute?.("role") === "combobox") {
      const container = control.closest?.<DomElement>(".ant-select, .el-select") || control.parentElement;
      const selected = container?.querySelector?.<DomElement>(".ant-select-selection-item, .el-select__selected-item");
      if (selected) return text(selected);
    }
    return String(control.value ?? text(control)).trim();
  }

  function captureValue(entry: DomElement | null | undefined) {
    const control = writableValueControl(entry);
    return control ? { control, value: readValueControl(control) } : null;
  }

  // 控件类型判定必须和采集器一致：采集器决定控件能不能写，这里决定写回器
  // 是否支持这种控件。两处用同一套取值，业务声明才能同时约束采集和写回。
  const controlKind = (control: DomElement | null | undefined) => {
    const role = String(control?.getAttribute?.("role") || "").toLowerCase();
    if (role === "combobox" || control?.tagName === "SELECT") return "select";
    if (control?.tagName === "TEXTAREA") return "textarea";
    if (control?.getAttribute?.("contenteditable") === "true") return "contenteditable";
    return String(control?.getAttribute?.("type") || "text").toLowerCase() || "text";
  };

  /**
   * 写回策略：后端下发的字段与控件类型白名单。
   *
   * 没传（或没应用清单）时退回内置的报废置换白名单。`controlKinds` 空集合
   * 表示业务声明了"不限制"，与没声明是两件事。
   */
  interface WritePolicy {
    fields?: ReadonlySet<string> | null;
    controlKinds?: ReadonlySet<string> | null;
  }

  const fieldAllowed = (field: string, policy: WritePolicy | undefined) =>
    !policy?.fields ? ALLOWED_VALUE_FIELDS.has(field) : policy.fields.has(field);

  async function executeValue(root: DomRoot, entry: DomElement | null | undefined, action: PageWriteAction, guard: Guard, policy?: WritePolicy) {
    if (!action || !fieldAllowed(action.field, policy)) return { ok: false, message: "该字段不在允许回填范围内" };
    if (!entry || !sameCollectedRecord(guard)) return { ok: false, message: guardError };
    const control = writableValueControl(entry);
    const custom = control?.getAttribute?.("role") === "combobox";
    if (!control || !writable(control, custom)) return { ok: false, message: "目标字段不可写或页面已变化" };
    const kinds = policy?.controlKinds;
    if (kinds && kinds.size && !kinds.has(controlKind(control))) {
      return { ok: false, message: "该控件类型暂不支持自动回填，请手工填写" };
    }
    if (!String(action.value ?? "").trim()) return { ok: false, message: "回填值不能为空" };
    const original = readValueControl(control);
    const originalRaw = control.tagName === "SELECT" ? String(control.value ?? "") : original;
    if (action.expectedValue != null && normalizeFieldValue(action.field, original) !== normalizeFieldValue(action.field, action.expectedValue)) {
      return { ok: false, code: "FIELD_VALUE_CHANGED", currentValue: original,
        message: "该字段当前值与采集时不同，已更新页面值，请核对后再次回填" };
    }
    const resolved = {
      control, trigger: customTrigger(control),
      listboxId: control.getAttribute?.("aria-controls") || control.getAttribute?.("aria-owns"),
    };
    const error = custom
      ? await selectCustomOption(root, resolved, action.field, action.value, guard)
      : dispatchValue(control, action.value, root);
    if (error) return { ok: false, message: error };
    await settle();
    if (!sameCollectedRecord(guard) || !connected(control)) return { ok: false, message: guardError };
    const after = readValueControl(control);
    if (normalizeFieldValue(action.field, after) !== normalizeFieldValue(action.field, action.value)) {
      if (sameCollectedRecord(guard)) {
        if (custom) {
          const restoreError = await selectCustomOption(root, resolved, action.field, original, guard);
          if (restoreError) return { ok: false, message: `回填后回读失败；${restoreError}` };
        } else if (control.tagName === "SELECT") {
          control.value = originalRaw;
          dispatchNative(control, root);
        } else {
          dispatchValue(control, original, root);
        }
        await settle();
        if (!sameCollectedRecord(guard) || !connected(control)) return { ok: false, message: `${guardError}；请人工核对已写入字段` };
        if (normalizeFieldValue(action.field, readValueControl(control)) !== normalizeFieldValue(action.field, original)) {
          return { ok: false, message: "回填后回读失败；原值恢复失败，请人工核对页面" };
        }
      }
      return { ok: false, message: "回填后回读失败" };
    }
    try { control.scrollIntoView?.({ behavior: "smooth", block: "center", inline: "nearest" }); } catch { control.scrollIntoView?.(); }
    control.focus?.({ preventScroll: true });
    const highlightTarget = control.closest?.<DomElement>(".ant-input-affix-wrapper, .ant-select, .ant-picker, .el-input, .el-select, .el-date-editor") || control;
    highlightTarget.animate?.([
      { outline: "3px solid #1677ff", outlineOffset: "3px", backgroundColor: "#e6f4ff" },
      { outline: "3px solid #1677ff", outlineOffset: "3px", backgroundColor: "#e6f4ff", offset: 0.85 },
      { outline: "3px solid transparent", outlineOffset: "3px" },
    ], { duration: 4000 });
    return { ok: true, message: "字段已回填并回读，已定位到页面字段", actions: [{ field: action.field, label: action.field, value: after, status: "FILLED" }] };
  }

  async function executeValueGroup(
    root: DomRoot,
    entries: Array<{ field: string; element: DomElement; expectedValue?: string | null }>,
    action: PageWriteGroupAction,
    guard: Guard,
    policy?: WritePolicy,
  ) {
    if (!action.fields.length || action.fields.length !== entries.length || !String(action.value || "").trim()) {
      return { ok: false, message: "组合字段回填参数不完整", actions: [] };
    }
    const prepared: Array<GroupPrepared | GroupPreparationFailure> = entries.map((entry) => {
      // 组合字段和单字段走同一份白名单。这里以前只认内置的报废置换表，
      // 别的业务（过户的发票代码/号码）一来就被判成"不在允许范围内"——
      // 而且报错信息里写着报废置换，排查的人会以为是业务选错了。
      if (!fieldAllowed(entry.field, policy)) return { error: `${entry.field}不在本业务允许回填范围内` };
      const control = writableValueControl(entry.element);
      const custom = control?.getAttribute?.("role") === "combobox";
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
    const written: GroupPrepared[] = [];
    const rollback = async (message: string) => {
      const errors: string[] = [];
      if (!sameCollectedRecord(guard)) return { ok: false, message: `${message}；页面身份已失效，已停止回滚写入`, actions: [] };
      for (const item of [...written].reverse()) {
        const restoreError = item.custom
          ? await selectCustomOption(root, item.resolved, item.field, item.original, guard)
          : dispatchValue(item.control, item.original, root);
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
      const error = item.custom
        ? await selectCustomOption(root, item.resolved, item.field, action.value, guard)
        : dispatchValue(item.control, action.value, root);
      if (error) return await rollback(String(error));
      written.push(item);
      await settle();
    }
    if (!sameCollectedRecord(guard)) return await rollback("页面身份已失效，已停止组合回填");
    const results = [];
    for (const item of successful) {
      const after = readValueControl(item.control);
      if (normalizeFieldValue(item.field, after) !== normalizeFieldValue(item.field, action.value)) return await rollback(`${item.field}回填后回读失败`);
      results.push({ field: item.field, label: item.field, value: after, status: "FILLED" });
      const target = item.control.closest?.<DomElement>(".ant-input-affix-wrapper, .ant-select, .ant-picker, .el-input, .el-select, .el-date-editor") || item.control;
      target.animate?.([
        { outline: "3px solid #1677ff", outlineOffset: "3px", backgroundColor: "#e6f4ff" },
        { outline: "3px solid transparent", outlineOffset: "3px" },
      ], { duration: 4000 });
    }
    return { ok: true, message: "组合字段已同时回填并回读", actions: results };
  }

  export const ReviewPageFieldWriter = { defaultWritableFields: ALLOWED_VALUE_FIELDS, executeValue, executeValueGroup, captureValue, triggerInvoiceVerification, controlKind };

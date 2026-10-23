import type { DomElement } from "./dom.ts";

const SELECT_CONTAINER = ".ant-select, .el-select";
const COMBOBOX = "[role='combobox'], input[aria-controls], input[aria-owns]";

/** 一个选择框的搜索 input、外层 combobox 和辅助 input 只能代表一个逻辑控件。 */
export function logicalFormControl(control: DomElement): DomElement {
  const container = control.closest?.<DomElement>(SELECT_CONTAINER);
  if (!container) return control;
  const candidates = Array.from(container.querySelectorAll?.<DomElement>(COMBOBOX) || []);
  return (
    candidates.find(
      (node) => node.tagName === "INPUT" && node.getAttribute("role") === "combobox",
    ) ||
    candidates.find((node) => node.getAttribute("role") === "combobox") ||
    candidates[0] ||
    control
  );
}

export function logicalFormControls(controls: DomElement[]): DomElement[] {
  return [...new Set(controls.map(logicalFormControl))];
}

export function isSelectControl(control: DomElement | null | undefined): boolean {
  return Boolean(
    control &&
      (control.tagName === "SELECT" ||
        control.getAttribute?.("role") === "combobox" ||
        control.getAttribute?.("aria-controls") ||
        control.getAttribute?.("aria-owns") ||
        control.closest?.(SELECT_CONTAINER)),
  );
}

/** label 供展示和比较；value 只用于原生选项身份，不能把框架搜索 input.value 当作选中项。 */
export function readFormControlValue(control: DomElement | null | undefined): string {
  if (!control) return "";
  if (control.tagName === "SELECT") {
    const option =
      control.selectedOptions?.[0] ||
      Array.from(control.options || []).find(
        (item) => String(item.value) === String(control.value),
      );
    return String(option?.textContent || control.value || "").trim();
  }
  if (isSelectControl(control)) {
    const container = control.closest?.<DomElement>(SELECT_CONTAINER);
    const scope =
      container ||
      control.closest?.<DomElement>(".ant-form-item, .el-form-item, .form-item, [data-form-item]");
    const selected = scope?.querySelector?.<DomElement>(
      ".ant-select-selection-item, .ant-select-selection-selected-value, .el-select__selected-item",
    );
    if (selected) {
      const label = String(selected.textContent || selected.innerText || "").trim();
      const title = String(selected.getAttribute?.("title") || "").trim();
      // title 可保留省略显示的完整名称；不读取搜索词或 placeholder。
      return title || label;
    }
    if (container?.classList?.contains("el-select")) {
      return String(container.querySelector<DomElement>(".el-input__inner")?.value || "").trim();
    }
    if (container) return "";
  }
  return String(control.value ?? control.textContent ?? "").trim();
}

/** A picker input is readonly because selection happens in its calendar. */
export function antDatePicker(control: DomElement | null | undefined): DomElement | null {
  const picker = control?.closest?.<DomElement>(".ant-picker");
  if (!picker?.classList?.contains("ant-picker") || picker.classList.contains("ant-picker-range"))
    return null;
  return picker.querySelectorAll("input").length === 1 ? picker : null;
}

/** Collection and writing must agree on the logical control, not its inner input type. */
export function formControlKind(control: DomElement | null | undefined): string {
  if (antDatePicker(control)) return "date";
  if (isSelectControl(control)) return "select";
  if (control?.tagName === "TEXTAREA") return "textarea";
  if (control?.getAttribute?.("contenteditable") === "true") return "contenteditable";
  return String(control?.getAttribute?.("type") || "text").toLowerCase() || "text";
}

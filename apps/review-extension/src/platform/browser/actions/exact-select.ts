import type { DomElement } from "../dom.ts";
import { createRandomId } from "../../../shared/random-id.ts";

export interface ExactOptionSnapshot {selectedValue: string; selectedLabel?: string; valueField?: string; optionLabel?: string; optionToken?: string;}
export type InspectExactOption = (control: DomElement, target?: {id: string; label: string; prepare: boolean; resolveLabel?: boolean}) => Promise<ExactOptionSnapshot>;

export class SelectInspectionError extends Error {
  readonly code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

/** 隔离世界不读取 Vue 私有对象，由后台执行只读的 MAIN-world 适配器。 */
const inspections = new WeakMap<DomElement, Promise<ExactOptionSnapshot>>();
const inspectControl: InspectExactOption = async (control, target) => {
  const token = createRandomId();
  control.setAttribute("data-review-select-token", token);
  try {
    const result = await chrome.runtime.sendMessage({type: "INSPECT_REVIEW_SELECT", token,
      expectedPageUrl: location.href, target});
    if (!result?.ok) throw new SelectInspectionError(result?.code || "SELECT_INSPECTION_FAILED",
      result?.message || "无法读取下拉框真实 ID，请重新加载扩展并刷新页面");
    return result;
  } finally {
    if (control.getAttribute("data-review-select-token") === token) control.removeAttribute("data-review-select-token");
  }
};

// Series loading and catalog searches both verify the live scope. A control owns one token,
// so serialize its MAIN-world requests while independent controls can still be read in parallel.
export const inspectExactOption: InspectExactOption = async (control, target) => {
  const pending = inspections.get(control);
  const current = (pending ? pending.catch(() => {}) : Promise.resolve()).then(() => inspectControl(control, target));
  inspections.set(control, current);
  try { return await current; }
  finally { if (inspections.get(control) === current) inspections.delete(control); }
};

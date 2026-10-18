import type { DomElement } from "../dom.ts";
import { createRandomId } from "../../../shared/random-id.ts";

export interface ExactOptionSnapshot {selectedValue: string; optionToken?: string;}
export type InspectExactOption = (control: DomElement, target?: {id: string; label: string; prepare: boolean}) => Promise<ExactOptionSnapshot>;

/** 隔离世界不读取 Vue 私有对象，由后台执行只读的 MAIN-world 适配器。 */
export const inspectExactOption: InspectExactOption = async (control, target) => {
  const token = createRandomId();
  control.setAttribute("data-review-select-token", token);
  try {
    const result = await chrome.runtime.sendMessage({type: "INSPECT_REVIEW_SELECT", token,
      expectedPageUrl: location.href, target});
    if (!result?.ok) throw new Error(result?.message || "无法读取下拉框真实 ID，请重新加载扩展并刷新页面");
    return result;
  } finally { control.removeAttribute("data-review-select-token"); }
};

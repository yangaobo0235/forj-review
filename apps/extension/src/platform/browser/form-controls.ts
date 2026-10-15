import type { DomElement } from "./dom.ts";

/** A picker input is readonly because selection happens in its calendar. */
export function antDatePicker(control: DomElement | null | undefined): DomElement | null {
  const picker = control?.closest?.<DomElement>(".ant-picker");
  if (!picker?.classList?.contains("ant-picker") || picker.classList.contains("ant-picker-range")) return null;
  return picker.querySelectorAll("input").length === 1 ? picker : null;
}

/** Collection and writing must agree on the logical control, not its inner input type. */
export function formControlKind(control: DomElement | null | undefined): string {
  if (antDatePicker(control)) return "date";
  const role = String(control?.getAttribute?.("role") || "").toLowerCase();
  if (role === "combobox" || control?.tagName === "SELECT") return "select";
  if (control?.tagName === "TEXTAREA") return "textarea";
  if (control?.getAttribute?.("contenteditable") === "true") return "contenteditable";
  return String(control?.getAttribute?.("type") || "text").toLowerCase() || "text";
}

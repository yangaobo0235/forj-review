import type { DomElement, DomRoot } from "../dom.ts";
import { antDatePicker } from "../form-controls.ts";

interface CalendarAccess {
  visible: (element: DomElement) => boolean;
  enabled: (element: DomElement) => boolean;
  pause: () => Promise<void>;
  guard: () => boolean;
}

export function calendarDate(value: string): string | null {
  const text = value.normalize("NFKC").replace(/\s+/g, "");
  const match = text.match(/^(\d{4})(?:([-/.])(\d{1,2})\2(\d{1,2})|年(\d{1,2})月(\d{1,2})日)$/);
  if (!match) return null;
  const year = match[1];
  const month = match[3] || match[5];
  const day = match[4] || match[6];
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day)) return null;
  return `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
}

/** Use public calendar UI events so Vue/React receive the committed date. */
export async function selectCalendarDate(
  root: DomRoot, control: DomElement, value: string, access: CalendarAccess,
): Promise<string | null> {
  const picker = antDatePicker(control);
  if (!picker) return "未找到受支持的单日期控件，请手工填写";
  const target = calendarDate(value);
  if (value && !target) return "日期格式无效，请使用有效的 YYYY-MM-DD 日期";
  if (!access.guard()) return "页面已变化，请重新审核";
  const mouseClick = (element: DomElement) => {
    const EventClass = root.defaultView?.MouseEvent || globalThis.MouseEvent;
    // Ant opens on mousedown and clears on mouseup; HTMLElement.click() alone
    // emits neither event and cannot commit these framework interactions.
    if (EventClass) {
      for (const type of ["mousedown", "mouseup"]) element.dispatchEvent(new EventClass(type, {
        bubbles: true, cancelable: true, button: 0, view: root.defaultView || null,
      }));
    }
    element.click();
  };
  if (!value) {
    const clear = picker.querySelector<DomElement>(".ant-picker-clear");
    if (!clear || !access.enabled(clear)) return "无法恢复空日期，请人工核对页面";
    mouseClick(clear);
    return null;
  }
  const popups = () => Array.from(root.querySelectorAll<DomElement>(".ant-picker-dropdown"))
    .filter(access.visible);
  const before = new Set(popups());
  const wasFocused = picker.classList.contains("ant-picker-focused");
  control.focus?.({ preventScroll: true });
  mouseClick(control);
  let popup: DomElement | undefined;
  for (let attempt = 0; attempt < 20 && !popup; attempt += 1) {
    await access.pause();
    if (!access.guard()) return "页面已变化，请重新审核";
    const candidates = popups().filter(item => !before.has(item) || wasFocused);
    if (candidates.length > 1) return "日期日历归属不明确，请手工填写";
    popup = candidates[0];
  }
  if (!popup) return "未找到当前日期控件的日历，请手工填写";
  const close = async () => {
    const EventClass = control.ownerDocument?.defaultView?.KeyboardEvent || globalThis.KeyboardEvent;
    if (EventClass) control.dispatchEvent(new EventClass("keydown", { key: "Escape", code: "Escape", keyCode: 27, bubbles: true }));
    control.blur?.();
    // Ant keeps the closing popup visible during its leave animation. Wait
    // before another write, otherwise the reused popup looks like a foreign one.
    for (let attempt = 0; attempt < 20; attempt += 1) {
      if (!access.guard()) return "页面已变化，请重新审核";
      if (!access.visible(popup!)) return null;
      await access.pause();
    }
    return "日期日历未关闭，请核对页面后重新采集";
  };
  const fail = async (message: string) => { if (access.guard()) await close(); return message; };
  const targetMonth = target!.slice(0, 7);
  // A year jump avoids clicking every month when filling an old registration date.
  // Every navigation must change the visible month; never keep clicking a stale panel.
  for (let step = 0; step < 220; step += 1) {
    if (!access.guard()) return "页面已变化，请重新审核";
    if (!access.visible(popup) || !access.enabled(picker)) return fail("日期控件已关闭或不可写，请重新采集");
    const panels = Array.from(popup.querySelectorAll<DomElement>(".ant-picker-date-panel"))
      .filter(access.visible);
    if (panels.length !== 1 || popup.querySelector(".ant-picker-time-panel")) {
      return fail("仅支持单日期日历，请手工填写此日期控件");
    }
    const panel = panels[0];
    const cells = Array.from(panel.querySelectorAll<DomElement>(".ant-picker-cell[title]"));
    const month = cells.find(cell => cell.classList.contains("ant-picker-cell-in-view"))?.getAttribute("title")?.slice(0, 7);
    if (!month || !/^\d{4}-\d{2}$/.test(month)) return fail("无法确定日历当前月份，请手工填写");
    if (month === targetMonth) {
      const matches = cells.filter(cell => cell.getAttribute("title") === target && cell.classList.contains("ant-picker-cell-in-view"));
      if (matches.length !== 1 || !access.enabled(matches[0]) || matches[0].classList.contains("ant-picker-cell-disabled")) {
        return fail("目标日期被禁用或存在歧义，请手工填写");
      }
      mouseClick(matches[0]);
      // Blur/focus later must not revert a date that only changed input.value.
      await access.pause();
      if (!access.guard()) return "页面已变化，请重新审核";
      return await close();
    }
    const direction = month < targetMonth ? "next" : "prev";
    const yearJump = month.slice(0, 4) !== targetMonth.slice(0, 4);
    const button = panel.querySelector<DomElement>(`.ant-picker-header-${yearJump ? "super-" : ""}${direction}-btn`);
    if (!button || !access.enabled(button)) return fail("无法切换到目标日期，请手工填写");
    mouseClick(button);
    let changed = false;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await access.pause();
      if (!access.guard()) return "页面已变化，请重新审核";
      const current = popup.querySelector(".ant-picker-date-panel .ant-picker-cell-in-view[title]")?.getAttribute("title")?.slice(0, 7);
      if (current && current !== month) { changed = true; break; }
    }
    if (!changed) return fail("日历切换未生效，已停止回填");
  }
  return fail("目标日期超出自动切换范围，请手工填写");
}

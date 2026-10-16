import type { ReviewTabState } from "../catalog/page-catalog.ts";

/** 只保存浏览器内的节点；跨消息仍使用现有业务身份和采集批次。 */
export interface ActiveReviewTab extends ReviewTabState {
  tab: HTMLElement | null;
  panel: HTMLElement | null;
}

function visible(element: HTMLElement): boolean {
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    if (node.hidden || node.getAttribute("aria-hidden") === "true") return false;
    const style = node.ownerDocument.defaultView?.getComputedStyle(node);
    if (style?.display === "none" || style?.visibility === "hidden" || style?.visibility === "collapse") return false;
  }
  return element.getClientRects().length > 0;
}

/** 从可见审核表单读取激活页签，排除顶部导航、隐藏旧弹层及非当前面板。 */
export function readActiveReviewTab(root: Document, labels: readonly string[]): ActiveReviewTab {
  const empty = { label: "", panelReady: false, tab: null, panel: null };
  if (!labels.length) return empty;
  const groups = Array.from(root.querySelectorAll?.<HTMLElement>('form [role="tablist"]') ?? [])
    .filter(group => visible(group) && Array.from(group.querySelectorAll('[role="tab"]'))
      .some(tab => labels.includes(tab.textContent?.trim() || "")));
  if (groups.length !== 1) return empty;
  const selected = Array.from(groups[0].querySelectorAll<HTMLElement>('[role="tab"][aria-selected="true"]'))
    .filter(visible);
  if (selected.length !== 1) return empty;
  const tab = selected[0];
  const label = tab.textContent?.trim() || "";
  const panelId = tab.getAttribute("aria-controls");
  const panel = panelId ? root.getElementById(panelId) : null;
  const form = tab.closest("form");
  const panelReady = Boolean(panel && form?.contains(panel)
    && panel.getAttribute("role") === "tabpanel"
    && tab.id && panel.getAttribute("aria-labelledby") === tab.id
    && panel.getAttribute("aria-hidden") === "false" && visible(panel));
  return { label, panelReady, tab, panel: panelReady ? panel : null };
}

/** 步骤一旦离开即永久作废本次采集，切回原页签也不能恢复旧回填权限。 */
export function guardActiveReviewTab(
  root: Document,
  labels: readonly string[],
  snapshot: ActiveReviewTab,
  onInvalidated: () => void,
): { isCurrent: () => boolean; disconnect: () => void } {
  let invalidated = false;
  const invalidate = () => {
    if (!invalidated) { invalidated = true; onInvalidated(); }
  };
  const isCurrent = () => {
    if (invalidated) return false;
    const current = readActiveReviewTab(root, labels);
    if (!snapshot.panelReady || !current.panelReady || current.label !== snapshot.label
      || current.tab !== snapshot.tab || current.panel !== snapshot.panel) invalidate();
    return !invalidated;
  };
  const Observer = root.defaultView?.MutationObserver;
  const observer = Observer ? new Observer(records => {
    for (const record of records) {
      // 同一批 DOM 更新内切走再切回，也应作废；忽略框架重复设置相同属性。
      if (record.type === "attributes" && (
        (record.target === snapshot.tab && record.attributeName === "aria-selected" && record.oldValue !== "true")
        || (record.target === snapshot.panel && record.attributeName === "aria-hidden" && record.oldValue !== "false")
      )) invalidate();
      if (record.type === "childList" && Array.from(record.removedNodes).some(node =>
        node === snapshot.tab || node === snapshot.panel || node.contains(snapshot.tab) || node.contains(snapshot.panel))) invalidate();
    }
    isCurrent();
  }) : null;
  observer?.observe(root, { subtree: true, childList: true, attributes: true, attributeOldValue: true,
    attributeFilter: ["aria-selected", "aria-controls", "aria-labelledby", "aria-hidden", "hidden", "class", "style", "role", "id"] });
  return { isCurrent, disconnect: () => observer?.disconnect() };
}

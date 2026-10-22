import type { DomRoot } from "../dom";
import type { WriteObservation } from "./page-field-writer";

/** Observe the page's own model-config request; never send requests or read response bodies.
 * Model selection replaces the specification form asynchronously. Its request must complete
 * before writing dependent controls; a fixed delay cannot establish that ordering.
 */
export function observeModelLinkage(root: DomRoot, guard: () => boolean): WriteObservation {
  const view = root.defaultView;
  if (!view?.PerformanceObserver || !view.performance) return {
    wait: async () => ({ok: false, code: "MODEL_LINKAGE_UNAVAILABLE", message: "无法观察车型配置加载状态，请重新采集并人工核对"}),
    dispose: () => {},
  };
  const started = view.performance.now(), origin = view.location.origin;
  let finish: (result: {ok: boolean; code?: string; message?: string}) => void;
  const completed = new Promise<{ok: boolean; code?: string; message?: string}>(resolve => {finish = resolve;});
  const observer = new view.PerformanceObserver(list => {
    for (const entry of list.getEntries() as PerformanceResourceTiming[]) {
      const url = new URL(entry.name, view.location.href);
      if (entry.startTime < started || url.origin !== origin || !url.pathname.endsWith("/vehicle-model/config/detail")
        || !["fetch", "xmlhttprequest"].includes(entry.initiatorType)) continue;
      finish(entry.responseStatus >= 400
        ? {ok: false, code: "MODEL_LINKAGE_FAILED", message: "业务页面车型配置加载失败，请核对业务页面后重试"}
        : {ok: true});
    }
  });
  observer.observe({type: "resource"});
  const timer = view.setTimeout(() => finish({ok: false, code: "MODEL_LINKAGE_TIMEOUT",
    message: "未确认业务页面车型配置请求完成，请等待页面加载后重新采集"}), 15000);
  return {
    wait: async () => {
      const result = await completed;
      if (!guard()) return {ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新采集"};
      if (result.ok) {
        // Let the completed response's Vue update commit before resolving child controls.
        await new Promise<void>(resolve => view.setTimeout(resolve, 0));
      }
      return guard() ? result : {ok: false, code: "PAGE_IDENTITY_CHANGED", message: "页面已变化，请重新采集"};
    },
    dispose: () => {view.clearTimeout(timer); observer.disconnect();},
  };
}

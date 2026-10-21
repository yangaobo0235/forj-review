import { onScopeDispose, shallowReactive, watch } from "vue";
import type { CapturedPage, CaptureSummary, ConfigurationEvent, ConfigurationSession, PageTemplate, SessionSummary } from "@forj/configuration-contracts";
import { command, request } from "../api/client.ts";
import { runCollectionTrial } from "../browser/bridge.ts";
import { isRunning } from "./presentation.ts";

export interface ScopeSelection {fields: string[]; samples: string[]; required: string[]; pageId: string; rollbackVersion: string}

/** 客户端生命周期；业务推理和配置判定由配置服务执行。 */
export function useWorkbench() {
  const initial = new URLSearchParams(location.search);
  const state = shallowReactive({
    sessionId: initial.get("session") ?? "", session: null as ConfigurationSession | null,
    sessions: [] as SessionSummary[], templates: [] as PageTemplate[], captures: [] as CaptureSummary[],
    caseSets: [] as string[],
    capture: null as CapturedPage | null, source: null as CapturedPage | null,
    scopeOpen: Boolean(initial.get("capture")), scopeOpener: null as HTMLElement | null, events: [] as ConfigurationEvent[],
    busy: "", error: "", connectionError: "", notice: "", connected: false,
    get running(): boolean { return isRunning(state.session); },
  });
  let operation = false;
  let live = true;
  let sessionReadVersion = 0;
  const lifecycle = new AbortController();
  onScopeDispose(() => { live = false; lifecycle.abort(); });

  async function inventory() {
    const [history, catalog, collected] = await Promise.all([
      request<SessionSummary[]>("/sessions", undefined, undefined, lifecycle.signal),
      request<{templates: PageTemplate[]; case_sets?: string[]}>("/templates", undefined, undefined, lifecycle.signal),
      request<CaptureSummary[]>("/captures", undefined, undefined, lifecycle.signal),
    ]);
    if (!live) return;
    state.sessions = history; state.templates = catalog.templates; state.captures = collected;
    state.caseSets = catalog.case_sets ?? [];
    state.connected = true; state.connectionError = "";
  }
  async function initialize() {
    try {
      await inventory();
      const captureId = initial.get("capture");
      if (captureId) {
        const value = await request<CapturedPage>(`/captures/${encodeURIComponent(captureId)}`, undefined, undefined, lifecycle.signal);
        if (live) state.capture = value;
      }
    } catch (e) {
      if (live) { state.connected = false; state.connectionError = e instanceof Error ? e.message : "连接失败"; }
    }
  }
  void initialize();

  // 每个会话持有独立的轮询游标和取消信号，切换时立即终止旧请求。
  watch(() => state.sessionId, (sid, _previous, onCleanup) => {
    if (!sid) return;
    let current = true, sequence = 0, captureId = "";
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    onCleanup(() => { current = false; clearTimeout(timer); controller.abort(); });
    async function poll() {
      const readVersion = sessionReadVersion;
      try {
        const [value, updates] = await Promise.all([
          request<ConfigurationSession>(`/sessions/${sid}`, undefined, undefined, controller.signal),
          request<ConfigurationEvent[]>(`/sessions/${sid}/events?after_seq=${sequence}`, undefined, undefined, controller.signal),
        ]);
        if (!current) return;
        // 较早发出的轮询不能覆盖命令刚返回的新修订。
        if (readVersion === sessionReadVersion && (!state.session || value.revision >= state.session.revision)) state.session = value;
        state.connected = true; state.connectionError = "";
        state.sessions = state.sessions.map(item => item.session_id === sid ? {...item, state: state.session!.state} : item);
        if (updates.length) {
          sequence = updates.at(-1)!.seq;
          state.events = [...state.events, ...updates].slice(-80);
        }
        const activeCapture = state.session!.capture_id;
        if (activeCapture && activeCapture !== captureId) {
          const original = await request<CapturedPage>(`/captures/${encodeURIComponent(activeCapture)}`, undefined, undefined, controller.signal);
          if (current && state.session?.capture_id === activeCapture) { state.source = original; captureId = activeCapture; }
        }
      } catch (e) {
        if (current) { state.connected = false; state.connectionError = e instanceof Error ? e.message : "连接配置服务失败"; }
      } finally { if (current) timer = setTimeout(() => { void poll(); }, 1500); }
    }
    void poll();
  }, { immediate: true, flush: "sync" });

  function navigate(sid: string) {
    if (sid !== state.sessionId) { state.session = null; state.source = null; state.events = []; }
    state.sessionId = sid; state.scopeOpen = false; state.error = ""; state.notice = "";
    history.replaceState(null, "", sid ? `/?session=${encodeURIComponent(sid)}` : "/");
  }
  async function run(label: string, work: () => Promise<void>) {
    if (operation) return;
    operation = true; state.busy = label; state.error = ""; state.notice = "";
    try { await work(); }
    catch (e) { if (live) state.error = e instanceof Error ? e.message : "操作失败，请重试"; }
    finally { operation = false; if (live) state.busy = ""; }
  }
  async function reloadSession(sid: string) {
    const readVersion = ++sessionReadVersion;
    const value = await request<ConfigurationSession>(`/sessions/${sid}`, undefined, undefined, lifecycle.signal);
    if (live && readVersion === sessionReadVersion && sid === state.sessionId && (!state.session || value.revision >= state.session.revision)) state.session = value;
  }
  async function saveScope(selection: ScopeSelection, update: boolean) {
    const capture = state.capture, session = state.session, sid = state.sessionId;
    if (!capture) throw new Error("请先从浏览器扩展采集业务页面");
    const body = {snapshot: capture.snapshot, capture_id: capture.capture_id,
      selected_fields: selection.fields, selected_samples: selection.samples, required_samples: selection.required};
    if (update && session) {
      await request(`/sessions/${sid}/page`, command(session.revision, body));
      await reloadSession(sid);
      if (live && sid === state.sessionId) {
        state.source = capture; state.scopeOpen = false;
        state.notice = "页面现场已更新，草稿保留，需要重新核对映射和验证。";
      }
    } else {
      const result = await request<{session_id: string}>("/sessions", {client_request_id: crypto.randomUUID(), ...body,
        page_id: selection.pageId || null, rollback_version: selection.rollbackVersion || null});
      if (!live) return;
      navigate(result.session_id); state.source = capture; await inventory();
    }
  }
  async function send(text: string) {
    const session = state.session, sid = state.sessionId;
    if (!session || !text.trim()) return;
    const path = session.pending?.kind === "question" ? `/interactions/${session.pending.id}/answer` : "/messages";
    const payload = session.pending?.kind === "question" ? {answer: text} : {text};
    await request(`/sessions/${sid}${path}`, command(session.revision, payload));
    await reloadSession(sid);
  }
  async function trial() {
    const session = state.session, source = state.source, sid = state.sessionId;
    if (!session?.pending || !source || source.capture_id !== session.capture_id) throw new Error("此会话缺少原标签页关联，请重新采集并更新页面现场");
    const task = session.pending;
    const result = await runCollectionTrial(source, task);
    await request(`/sessions/${sid}/browser-tasks/${task.id}/result`, command(session.revision, result));
    await reloadSession(sid);
    if (live && sid === state.sessionId) state.notice = task.manual ? "只读试采集完成，可继续独立回放与验证。" : "只读试采集结果已送达，助手将继续核对。";
  }
  async function confirm() {
    const session = state.session, sid = state.sessionId;
    if (!session?.report) return;
    await request(`/sessions/${sid}/confirm`, command(session.revision, {
      report_id: session.report.report_id, candidate_digest: session.report.candidate_digest,
    }));
    await reloadSession(sid);
  }
  async function manual(kind: "draft" | "mappings" | "trial" | "replay", body: Record<string, unknown> = {}) {
    const session = state.session, sid = state.sessionId;
    if (!session) return;
    await request(`/sessions/${sid}/${kind}`, command(session.revision, body));
    await reloadSession(sid);
  }
  async function action(kind: "resume" | "cancel" | "validate" | "activation" | "delete") {
    const session = state.session, sid = state.sessionId;
    if (!session) return;
    const path = `/sessions/${sid}`;
    if (kind === "delete") {
      await request(path, undefined, "DELETE");
      if (live && sid === state.sessionId) navigate("");
      await inventory(); return;
    }
    if (kind === "activation") {
      const result = await request<{reason: string}>(path + "/activation");
      if (live && sid === state.sessionId) state.notice = result.reason;
    } else await request(path + "/" + kind, kind === "cancel" ? {} : command(session.revision));
    await reloadSession(sid);
  }
  async function openScope() {
    // 请求期间按钮会禁用，必须在等待接口前记住弹窗的触发元素。
    const opener = typeof document === "undefined" ? null : document.activeElement;
    state.scopeOpener = typeof HTMLElement !== "undefined" && opener instanceof HTMLElement ? opener : null;
    await inventory();
    if (!live) return;
    if (state.source) state.capture = state.source;
    state.scopeOpen = true;
  }
  async function selectCapture(id: string) {
    const value = await request<CapturedPage>(`/captures/${encodeURIComponent(id)}`, undefined, undefined, lifecycle.signal);
    if (live) state.capture = value;
  }
  return Object.assign(state, {setScopeOpen: (value: boolean) => { state.scopeOpen = value; },
    setNotice: (value: string) => { state.notice = value; }, run, inventory, navigate, saveScope,
    send, trial, confirm, manual, action, openScope, selectCapture});
}
export type Workbench = ReturnType<typeof useWorkbench>;

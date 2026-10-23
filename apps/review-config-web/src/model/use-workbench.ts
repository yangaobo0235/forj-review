import { onScopeDispose, shallowReactive, watch } from "vue";
import type {
  CapturedPage,
  CaptureSummary,
  ConfigurationEvent,
  ConfigurationTask,
  PageTemplate,
  TaskSummary,
} from "@forj/configuration-contracts";
import { command, request, RequestError } from "../api/client.ts";
import { readTask } from "../api/tasks";
import { retryableCommand } from "../api/retryable-command";
import { checkBrowserAdoption, runCollectionTrial } from "../browser/bridge.ts";
import { isRunning } from "./presentation.ts";

export interface TaskStart {
  pageId: string;
  message: string;
  rollbackVersion?: string;
  parentSessionId?: string;
}

/** 客户端生命周期；业务推理和配置判定由配置服务执行。 */
export function useWorkbench() {
  const initial = new URLSearchParams(location.search);
  const state = shallowReactive({
    sessionId: initial.get("task") ?? "",
    referenceDraft: [] as Array<{
      kind: "field" | "sample";
      id: string;
      label: string;
      captureId: string;
    }>,
    session: null as ConfigurationTask | null,
    sessions: [] as TaskSummary[],
    templates: [] as PageTemplate[],
    captures: [] as CaptureSummary[],
    caseSets: [] as string[],
    capture: null as CapturedPage | null,
    source: null as CapturedPage | null,
    scopeOpen: false,
    scopeOpener: null as HTMLElement | null,
    events: [] as ConfigurationEvent[],
    hasEarlierEvents: false,
    loadingEarlierEvents: false,
    busy: "",
    error: "",
    connectionError: "",
    notice: initial.has("session") ? "旧任务已清理，请选择采集创建新的修改任务。" : "",
    connected: false,
    messageDraft: "",
    get running(): boolean {
      return isRunning(state.session);
    },
  });
  // Exact retired configuration prefixes only. Ordinary review settings are unrelated.
  for (let index = localStorage.length - 1; index >= 0; index--) {
    const key = localStorage.key(index);
    if (
      (key &&
        ["review.message.", "review.publish.", "review.fork."].some((prefix) =>
          key.startsWith(prefix),
        )) ||
      key === "review.create"
    )
      localStorage.removeItem(key!);
  }
  function loadInput(sid: string) {
    try {
      const input = JSON.parse(localStorage.getItem(`review.workspace.v2.input.${sid}`) || "{}");
      state.messageDraft = typeof input.text === "string" ? input.text : "";
      state.referenceDraft = Array.isArray(input.refs)
        ? input.refs.filter(
            (r: Record<string, unknown>) =>
              r &&
              ["field", "sample"].includes(String(r.kind)) &&
              typeof r.id === "string" &&
              typeof r.captureId === "string" &&
              typeof r.label === "string",
          )
        : [];
    } catch {
      state.messageDraft = "";
      state.referenceDraft = [];
    }
  }
  loadInput(state.sessionId);
  watch(
    () => [state.messageDraft, state.referenceDraft],
    () => {
      localStorage.setItem(
        `review.workspace.v2.input.${state.sessionId}`,
        JSON.stringify({ text: state.messageDraft, refs: state.referenceDraft }),
      );
    },
  );
  const taskCommand = (task: ConfigurationTask, extra = {}) =>
    command(task.revision, { expected_row_version: task.row_version, ...extra });
  let operation = false;
  let live = true;
  let sessionReadVersion = 0;
  let captureReadVersion = 0;
  const lifecycle = new AbortController();
  onScopeDispose(() => {
    live = false;
    lifecycle.abort();
  });

  async function inventory() {
    const [history, catalog, collected] = await Promise.all([
      request<TaskSummary[]>("/tasks", undefined, undefined, lifecycle.signal),
      request<{ templates: PageTemplate[]; case_sets?: string[] }>(
        "/templates",
        undefined,
        undefined,
        lifecycle.signal,
      ),
      request<CaptureSummary[]>("/captures", undefined, undefined, lifecycle.signal),
    ]);
    if (!live) return;
    state.sessions = history;
    state.templates = catalog.templates;
    state.captures = collected;
    state.caseSets = catalog.case_sets ?? [];
    state.connected = true;
    state.connectionError = "";
  }
  async function initialize() {
    try {
      await inventory();
      const captureId = initial.get("capture");
      if (captureId) {
        const value = await request<CapturedPage>(
          `/captures/${encodeURIComponent(captureId)}`,
          undefined,
          undefined,
          lifecycle.signal,
        );
        if (live) state.capture = value;
      }
    } catch (e) {
      if (live) {
        state.connected = false;
        state.connectionError = e instanceof Error ? e.message : "连接失败";
      }
    }
  }
  void initialize();

  // 每个会话持有独立的轮询游标和取消信号，切换时立即终止旧请求。
  watch(
    () => state.sessionId,
    (sid, _previous, onCleanup) => {
      if (!sid) return;
      let current = true,
        sequence = 0,
        captureId = "";
      let initialEvents = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const controller = new AbortController();
      onCleanup(() => {
        current = false;
        clearTimeout(timer);
        controller.abort();
      });
      async function poll() {
        const readVersion = sessionReadVersion;
        try {
          const [value, updates] = await Promise.all([
            readTask(sid, controller.signal),
            request<ConfigurationEvent[]>(
              initialEvents
                ? `/tasks/${sid}/events?before_seq=0&limit=100&include_messages=false`
                : `/tasks/${sid}/events?after_seq=${sequence}&include_messages=false`,
              undefined,
              undefined,
              controller.signal,
            ),
          ]);
          if (!current) return;
          // 较早发出的轮询不能覆盖命令刚返回的新修订。
          if (
            readVersion === sessionReadVersion &&
            (!state.session || value.revision >= state.session.revision)
          )
            state.session = value;
          state.connected = true;
          state.connectionError = "";
          state.sessions = state.sessions.map((item) =>
            item.task_id === sid
              ? {
                  ...item,
                  state: state.session!.state,
                  label: state.session!.task.label,
                }
              : item,
          );
          if (updates.length) {
            sequence = updates.at(-1)!.seq;
            state.events = [
              ...new Map([...state.events, ...updates].map((event) => [event.seq, event])).values(),
            ].sort((a, b) => a.seq - b.seq);
          }
          if (initialEvents) state.hasEarlierEvents = updates.length === 100;
          initialEvents = false;
          const activeCapture = state.session!.capture_id;
          if (!activeCapture) {
            state.source = null;
            captureId = "";
          }
          if (activeCapture && activeCapture !== captureId) {
            const original = await request<CapturedPage>(
              `/captures/${encodeURIComponent(activeCapture)}`,
              undefined,
              undefined,
              controller.signal,
            );
            if (current && state.session?.capture_id === activeCapture) {
              state.source = original;
              captureId = activeCapture;
            }
          }
        } catch (e) {
          if (current && e instanceof RequestError && e.status === 404) {
            current = false;
            state.notice = "来源任务已清理或不存在，请选择采集创建新的修改任务。";
            state.session = null;
            state.source = null;
            state.sessionId = "";
            history.replaceState(null, "", "/");
          } else if (current) {
            state.connected = false;
            state.connectionError = e instanceof Error ? e.message : "连接配置服务失败";
          }
        } finally {
          if (current)
            timer = setTimeout(() => {
              void poll();
            }, 1500);
        }
      }
      void poll();
    },
    { immediate: true, flush: "sync" },
  );

  function navigate(sid: string) {
    captureReadVersion++;
    if (sid !== state.sessionId) {
      localStorage.setItem(
        `review.workspace.v2.input.${state.sessionId}`,
        JSON.stringify({ text: state.messageDraft, refs: state.referenceDraft }),
      );
      loadInput(sid);
      state.session = null;
      state.source = null;
      state.events = [];
      state.hasEarlierEvents = false;
      state.loadingEarlierEvents = false;
    }
    state.sessionId = sid;
    state.scopeOpen = false;
    state.error = "";
    state.notice = "";
    history.replaceState(null, "", sid ? `/?task=${encodeURIComponent(sid)}` : "/");
  }
  async function loadEarlierEvents() {
    const sid = state.sessionId;
    const before = state.events[0]?.seq;
    if (!before || state.loadingEarlierEvents || !state.hasEarlierEvents) return;
    state.loadingEarlierEvents = true;
    try {
      const updates = await request<ConfigurationEvent[]>(
        `/tasks/${sid}/events?before_seq=${before}&limit=100&include_messages=false`,
        undefined,
        undefined,
        lifecycle.signal,
      );
      if (!live || state.sessionId !== sid) return;
      state.events = [
        ...new Map([...updates, ...state.events].map((event) => [event.seq, event])).values(),
      ].sort((a, b) => a.seq - b.seq);
      state.hasEarlierEvents = updates.length === 100;
    } finally {
      if (state.sessionId === sid) state.loadingEarlierEvents = false;
    }
  }
  async function run(label: string, work: () => Promise<void>) {
    if (operation) return;
    operation = true;
    state.busy = label;
    state.error = "";
    state.notice = "";
    try {
      await work();
    } catch (e) {
      if (live) state.error = e instanceof Error ? e.message : "操作失败，请重试";
    } finally {
      operation = false;
      if (live) state.busy = "";
    }
  }
  async function reloadSession(sid: string) {
    const readVersion = ++sessionReadVersion;
    const value = await readTask(sid, lifecycle.signal);
    if (
      live &&
      readVersion === sessionReadVersion &&
      sid === state.sessionId &&
      (!state.session || value.revision >= state.session.revision)
    )
      state.session = value;
  }
  async function startTask(selection: TaskStart, capture = state.capture) {
    if (!capture) throw new Error("请先选择一份页面采集，再开始修改");
    const input = {
      page_id: selection.pageId,
      initial_message: selection.message.trim() || null,
      parent_task_id: selection.parentSessionId || null,
      rollback_version: selection.rollbackVersion || null,
      ...(capture ? { snapshot: capture.snapshot, capture_id: capture.capture_id } : {}),
    };
    const result = await retryableCommand<{ task_id: string }>(
      "review.workspace.v2.request.create",
      "/tasks",
      { client_request_id: crypto.randomUUID(), ...input },
      JSON.stringify(input),
    );
    if (!live) return;
    if (!state.sessionId && state.messageDraft === selection.message) {
      state.messageDraft = "";
      state.referenceDraft = [];
    }
    navigate(result.task_id);
    state.source = capture;
    await inventory();
  }
  async function attachCapture(capture = state.capture) {
    const session = state.session,
      sid = state.sessionId;
    if (!capture || !session) throw new Error("请选择从扩展采集的业务页面");
    if (
      capture.snapshot.detected_page_id &&
      capture.snapshot.detected_page_id !== session.draft.page_id
    )
      throw new Error("现场所属页面与当前任务不一致，请重新选择");
    await request(
      `/tasks/${sid}/page`,
      taskCommand(session, {
        snapshot: capture.snapshot,
        capture_id: capture.capture_id,
      }),
    );
    await reloadSession(sid);
    if (live && sid === state.sessionId) {
      state.source = capture;
      state.scopeOpen = false;
      state.notice = "现场已关联，需求与候选保留，将按当前现场继续核对。";
    }
  }
  async function send(text: string) {
    const session = state.session,
      sid = state.sessionId;
    if (!session || !text.trim()) return;
    const answering = session.pending?.kind === "question" && !state.running;
    const path = answering ? `/interactions/${session.pending!.id}/answer` : "/messages";
    const payload = answering
      ? { answer: text }
      : { text, expected_intent_revision: session.intent_revision };
    const references = {
      reference_fields: state.referenceDraft.filter((r) => r.kind === "field").map((r) => r.id),
      reference_samples: state.referenceDraft.filter((r) => r.kind === "sample").map((r) => r.id),
    };
    if (state.referenceDraft.some((r) => r.captureId !== session.capture_id))
      throw new Error("引用的现场已变化，请重新引用当前资料");
    await retryableCommand(
      `review.workspace.v2.request.message.${sid}`,
      `/tasks/${sid}${path}`,
      taskCommand(session, {
        ...payload,
        ...references,
        ...(state.running ? { expected_row_version: undefined } : {}),
      }),
      JSON.stringify({ ...payload, ...references }),
    );
    state.referenceDraft = [];
    await reloadSession(sid);
  }
  async function trial() {
    const session = state.session,
      source = state.source,
      sid = state.sessionId;
    if (!session?.pending || !source || source.capture_id !== session.capture_id)
      throw new Error("此会话缺少原标签页关联，请重新采集并更新页面现场");
    const task = session.pending;
    const result = await runCollectionTrial(source, task);
    await request(`/tasks/${sid}/browser-tasks/${task.id}/result`, taskCommand(session, result));
    await reloadSession(sid);
    if (live && sid === state.sessionId)
      state.notice = task.manual
        ? "只读试采集完成，可继续独立回放与验证。"
        : "只读试采集结果已送达，助手将继续核对。";
  }
  async function confirm() {
    const session = state.session,
      sid = state.sessionId;
    if (!session?.report) return;
    const key = `review.workspace.v2.request.publish.${sid}.${session.report.report_id}`;
    const requestId = localStorage.getItem(key) || crypto.randomUUID();
    localStorage.setItem(key, requestId);
    await request(`/tasks/${sid}/confirm`, {
      ...taskCommand(session, {
        report_id: session.report.report_id,
        candidate_digest: session.report.candidate_digest,
      }),
      client_request_id: requestId,
    });
    await reloadSession(sid);
  }
  async function adoption() {
    const session = state.session,
      sid = state.sessionId;
    if (!session || !state.source) throw new Error("请先关联业务浏览器现场");
    await action("activation");
    const task = state.session?.adoption_task;
    if (!task)
      throw new Error(state.session?.activation?.reason || "请先确认审核服务已加载本次发布");
    const result = await checkBrowserAdoption(state.source, task);
    await request(`/tasks/${sid}/adoption`, taskCommand(session, result));
    await reloadSession(sid);
  }
  async function reference(fields: string[] = [], samples: string[] = []) {
    const session = state.session,
      sid = state.sessionId;
    if (!session) return;
    await request(`/tasks/${sid}/references`, taskCommand(session, { fields, samples }));
    await reloadSession(sid);
  }
  async function fork(kind: "split" | "rebase", body: Record<string, unknown>) {
    const session = state.session;
    if (!session) return;
    const value = await retryableCommand<{ task_id: string }>(
      `review.workspace.v2.request.fork.${session.task_id}.${kind}`,
      `/tasks/${session.task_id}/${kind}`,
      taskCommand(session, {
        ...body,
        expected_intent_revision: session.intent_revision,
      }),
      JSON.stringify(body),
    );
    navigate(value.task_id);
    await inventory();
  }
  async function importLocalReport(report: Record<string, unknown>) {
    const session = state.session,
      sid = state.sessionId;
    if (!session) return;
    await request(`/tasks/${sid}/local-report`, taskCommand(session, { report }));
    await reloadSession(sid);
    await inventory();
  }
  async function manual(
    kind: "draft" | "mappings" | "trial" | "replay",
    body: Record<string, unknown> = {},
  ) {
    const session = state.session,
      sid = state.sessionId;
    if (!session) return;
    await request(`/tasks/${sid}/${kind}`, taskCommand(session, body));
    await reloadSession(sid);
  }
  async function action(
    kind: "resume" | "cancel" | "validate" | "activation" | "delete" | "archive" | "restore",
  ) {
    const session = state.session,
      sid = state.sessionId;
    if (!session) return;
    const path = `/tasks/${sid}`;
    if (kind === "delete") {
      await request(path, undefined, "DELETE");
      if (live && sid === state.sessionId) navigate("");
      await inventory();
      return;
    }
    if (kind === "activation") {
      const result = await request<{ reason: string }>(path + "/activation");
      if (live && sid === state.sessionId) state.notice = result.reason;
    } else await request(path + "/" + kind, kind === "cancel" ? {} : taskCommand(session));
    await reloadSession(sid);
    if (kind === "activation") await inventory();
  }
  async function openScope() {
    // 请求期间按钮会禁用，必须在等待接口前记住弹窗的触发元素。
    const opener = typeof document === "undefined" ? null : document.activeElement;
    state.scopeOpener =
      typeof HTMLElement !== "undefined" && opener instanceof HTMLElement ? opener : null;
    await inventory();
    if (!live) return;
    if (state.source) state.capture = state.source;
    state.scopeOpen = true;
  }
  async function selectCapture(id: string) {
    const readVersion = ++captureReadVersion;
    const value = await request<CapturedPage>(
      `/captures/${encodeURIComponent(id)}`,
      undefined,
      undefined,
      lifecycle.signal,
    );
    if (live && readVersion === captureReadVersion) state.capture = value;
  }
  const attemptedTrials = new Set<string>();
  watch(
    () => [state.session?.pending?.id, state.source?.capture_id, state.busy, state.running],
    () => {
      const pending = state.session?.pending;
      if (
        pending?.kind !== "browser" ||
        !state.source ||
        state.busy ||
        state.running ||
        attemptedTrials.has(pending.id)
      )
        return;
      attemptedTrials.add(pending.id);
      void run("自动执行只读页面验证", trial);
    },
  );
  const checkedReleases = new Set<string>();
  watch(
    () => [state.session?.receipt?.operation_id, state.busy, state.running],
    () => {
      const session = state.session;
      if (!session?.receipt || state.busy || state.running || checkedReleases.has(session.task_id))
        return;
      checkedReleases.add(session.task_id);
      void run("核对服务加载", () => action("activation"));
    },
  );
  return Object.assign(state, {
    setScopeOpen: (value: boolean) => {
      state.scopeOpen = value;
    },
    setNotice: (value: string) => {
      state.notice = value;
    },
    setMessageDraft: (value: string) => {
      state.messageDraft = value;
    },
    run,
    inventory,
    loadEarlierEvents,
    navigate,
    send,
    trial,
    confirm,
    manual,
    action,
    openScope,
    selectCapture,
    startTask,
    attachCapture,
    adoption,
    reference,
    quote: (kind: "field" | "sample", id: string, label: string) => {
      const captureId = state.session?.capture_id;
      if (
        captureId &&
        !state.referenceDraft.some(
          (r) => r.kind === kind && r.id === id && r.captureId === captureId,
        )
      )
        state.referenceDraft = [...state.referenceDraft, { kind, id, label, captureId }];
    },
    removeQuote: (id: string) => {
      state.referenceDraft = state.referenceDraft.filter((r) => r.id !== id);
    },
    fork,
    importLocalReport,
  });
}
export type Workbench = ReturnType<typeof useWorkbench>;

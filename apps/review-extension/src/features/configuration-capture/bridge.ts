import type { BrowserBridgeRequest, ConfigurationSnapshot } from "@forj/configuration-contracts";
import { createRandomId } from "../../shared/random-id.ts";
import { reviewApiBaseUrl } from "../../shared/review-api-url.ts";
import { fetchPageCatalog } from "../../platform/browser/catalog/page-catalog.ts";
import { fetchCollectManifest } from "../../platform/browser/catalog/collect-manifest.ts";

const backend = "http://127.0.0.1:26102";
const workbench = "http://127.0.0.1:26198";
const bindingKey = (id: string) => `configuration.url-capture.${id}`;

interface Binding {
  tab_id: number;
  snapshot_id: string;
  page_instance_id: string;
  page_url: string;
  structure_digest: string;
}

export async function captureAndOpenWorkbench(progress: (text: string) => void = () => {}) {
  const stored = await chrome.storage.local.get(null);
  const retired = Object.keys(stored).filter((key) => key.startsWith("configuration.capture."));
  if (retired.length) await chrome.storage.local.remove(retired);
  progress("正在采集字段和材料目录…");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !/^https?:/.test(tab.url ?? "")) throw new Error("请先打开需要配置的业务页面");
  let snapshot: ConfigurationSnapshot & { error?: string };
  try {
    snapshot = await chrome.tabs.sendMessage(tab.id, {
      type: "DISCOVER_CONFIGURATION_PAGE",
      pageCatalog: await fetchPageCatalog(reviewApiBaseUrl),
    });
  } catch {
    throw new Error("无法读取页面，请刷新业务页面后重新采集");
  }
  if (!snapshot || snapshot.error) throw new Error(snapshot?.error || "页面未返回采集结果");
  let response: Response;
  try {
    response = await fetch(backend + "/api/review-config/captures", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        client_request_id: createRandomId(),
        extension_id: chrome.runtime.id,
        snapshot,
      }),
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new Error("无法连接配置服务，请先启动本机 26102 配置后端");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "页面采集保存失败");
  const binding: Binding = {
    tab_id: tab.id,
    snapshot_id: snapshot.snapshot_id,
    page_instance_id: snapshot.page_instance_id,
    page_url: snapshot.page_url,
    structure_digest: snapshot.structure_digest,
  };
  await chrome.storage.local.set({ [bindingKey(result.capture_id)]: binding });
  progress("字段与图片地址已保存，正在打开工作台…");
  await chrome.tabs.create({
    url: `${workbench}/?capture=${encodeURIComponent(result.capture_id)}`,
  });
  return { problems: snapshot.diagnostics };
}

/** The web app may ask only for a read-only trial in the original captured tab. */
export async function handleConfigurationRequest(
  message: BrowserBridgeRequest,
  senderUrl: string | undefined,
) {
  if (!senderUrl || ![workbench, "http://localhost:26198"].includes(new URL(senderUrl).origin)) {
    throw new Error("仅配置工作台可以发起试采集");
  }
  if (
    !["CONFIGURATION_COLLECTION_TRIAL", "CONFIGURATION_ADOPTION_CHECK"].includes(message?.type) ||
    !/^[a-f0-9]{32}$/.test(message.capture_id ?? "")
  )
    throw new Error("只读采集请求无效");
  if (
    message.type === "CONFIGURATION_COLLECTION_TRIAL" &&
    (message.task?.kind !== "browser" || !message.task.manifest)
  )
    throw new Error("试采集请求无效");
  if (message.type === "CONFIGURATION_ADOPTION_CHECK" && message.task?.kind !== "adoption")
    throw new Error("采用核对请求无效");
  const stored = await chrome.storage.local.get(bindingKey(message.capture_id));
  const binding = stored[bindingKey(message.capture_id)] as Binding | undefined;
  if (!binding) throw new Error("找不到原业务标签页的关联，请回到业务页面重新采集");
  for (const key of ["snapshot_id", "page_instance_id", "page_url"] as const) {
    if (message.task[key] !== binding[key])
      throw new Error("试采集任务与原页面不一致，请更新页面现场");
  }
  if (
    message.type === "CONFIGURATION_COLLECTION_TRIAL" &&
    message.task.structure_digest !== binding.structure_digest
  )
    throw new Error("试采集任务与页面结构不一致，请更新现场");
  let tab: chrome.tabs.Tab;
  try {
    tab = await chrome.tabs.get(binding.tab_id);
  } catch {
    throw new Error("原业务标签页已关闭，请重新打开并采集");
  }
  if (tab.url !== binding.page_url) throw new Error("原业务标签页已切换记录，请重新采集");
  if (message.type === "CONFIGURATION_ADOPTION_CHECK") {
    // Fetch the actual deployed manifest independently of workbench-provided data.
    const catalog = await fetchPageCatalog(reviewApiBaseUrl);
    const identities = catalog?.identities.filter((p) => p.page_id === message.task.page_id) ?? [];
    if (identities.length !== 1) throw new Error("当前服务目录无法唯一确认目标页面");
    const identity = identities[0]!;
    const manifest = await fetchCollectManifest(
      reviewApiBaseUrl,
      identity.business_type,
      fetch,
      identity,
    );
    if (
      !manifest ||
      manifest.profile_version !== message.task.version ||
      manifest.effective_digest !== message.task.effective_digest
    )
      throw new Error("业务浏览器取得的发布版本或摘要不一致，请重新核对服务加载");
    const result = await chrome.tabs.sendMessage(binding.tab_id, {
      type: "TRIAL_CONFIGURATION_PAGE",
      task: {
        ...message.task,
        kind: "browser",
        manifest,
        page_catalog: catalog,
        candidate_digest: message.task.effective_digest,
        structure_digest: binding.structure_digest,
      },
    });
    if (!result || result.error) throw new Error(result?.error || "采用核对未返回采集事实");
    return {
      task_id: message.task.id,
      snapshot_id: message.task.snapshot_id,
      page_instance_id: result.page_instance_id,
      page_url: result.page_url,
      page_id: result.matched_page_id,
      version: manifest.profile_version,
      effective_digest: manifest.effective_digest,
      fields: result.fields,
      diagnostics: result.diagnostics,
    };
  }
  let result;
  try {
    result = await chrome.tabs.sendMessage(binding.tab_id, {
      type: "TRIAL_CONFIGURATION_PAGE",
      task: message.task,
    });
  } catch {
    throw new Error("原业务页面已刷新或插件已重载，请重新采集并更新会话现场");
  }
  if (!result || result.error) throw new Error(result?.error || "业务页面未返回试采集结果");
  return result;
}

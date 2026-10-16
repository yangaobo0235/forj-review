import type { BrowserBridgeRequest, ConfigurationSnapshot } from "@forj/configuration-contracts";

const backend = "http://127.0.0.1:8011";
const workbench = "http://127.0.0.1:5174";
const bindingKey = (id: string) => `configuration.capture.${id}`;

interface Binding {
  tab_id: number; snapshot_id: string; page_instance_id: string; page_url: string; structure_digest: string;
}

export async function captureAndOpenWorkbench() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !/^https?:/.test(tab.url ?? "")) throw new Error("请先打开需要配置的业务页面");
  let snapshot: ConfigurationSnapshot & {error?: string};
  try {snapshot = await chrome.tabs.sendMessage(tab.id, {type: "DISCOVER_CONFIGURATION_PAGE"});}
  catch {throw new Error("无法读取页面，请刷新业务页面后重新采集");}
  if (!snapshot || snapshot.error) throw new Error(snapshot?.error || "页面未返回采集结果");
  let response: Response;
  try {
    response = await fetch(backend + "/api/configuration-agent/captures", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({client_request_id: crypto.randomUUID(), extension_id: chrome.runtime.id, snapshot}),
      signal: AbortSignal.timeout(15000),
    });
  } catch {throw new Error("无法连接配置服务，请先启动本机 8011 配置后端");}
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "页面采集保存失败");
  const binding: Binding = {tab_id: tab.id, snapshot_id: snapshot.snapshot_id,
    page_instance_id: snapshot.page_instance_id, page_url: snapshot.page_url, structure_digest: snapshot.structure_digest};
  await chrome.storage.local.set({[bindingKey(result.capture_id)]: binding});
  await chrome.tabs.create({url: `${workbench}/?capture=${encodeURIComponent(result.capture_id)}`});
}

/** The web app may ask only for a read-only trial in the original captured tab. */
export async function handleCollectionTrial(message: BrowserBridgeRequest, senderUrl: string | undefined) {
  if (!senderUrl || ![workbench, "http://localhost:5174"].includes(new URL(senderUrl).origin)) {
    throw new Error("仅配置工作台可以发起试采集");
  }
  if (message?.type !== "CONFIGURATION_COLLECTION_TRIAL" || !/^[a-f0-9]{32}$/.test(message.capture_id ?? "")
      || message.task?.kind !== "browser" || !message.task.manifest) throw new Error("试采集请求无效");
  const stored = await chrome.storage.local.get(bindingKey(message.capture_id));
  const binding = stored[bindingKey(message.capture_id)] as Binding | undefined;
  if (!binding) throw new Error("找不到原业务标签页的关联，请回到业务页面重新采集");
  for (const key of ["snapshot_id", "page_instance_id", "page_url", "structure_digest"] as const) {
    if (message.task[key] !== binding[key]) throw new Error("试采集任务与原页面不一致，请更新页面现场");
  }
  let tab: chrome.tabs.Tab;
  try {tab = await chrome.tabs.get(binding.tab_id);}
  catch {throw new Error("原业务标签页已关闭，请重新打开并采集");}
  if (tab.url !== binding.page_url) throw new Error("原业务标签页已切换记录，请重新采集");
  let result;
  try {result = await chrome.tabs.sendMessage(binding.tab_id, {type: "TRIAL_CONFIGURATION_PAGE", task: message.task});}
  catch {throw new Error("原业务页面已刷新或插件已重载，请重新采集并更新会话现场");}
  if (!result || result.error) throw new Error(result?.error || "业务页面未返回试采集结果");
  return result;
}

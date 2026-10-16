import type { CapturedPage, CollectionTrialResult, ConfigurationPending } from "@forj/configuration-contracts";

/** Only a Chrome extension may access the original business tab. No DOM collection lives here. */
export function runCollectionTrial(capture: CapturedPage, task: ConfigurationPending): Promise<CollectionTrialResult> {
  if (!globalThis.chrome?.runtime?.sendMessage) return Promise.reject(new Error("请在 Chrome 中打开工作台，并重新加载审核助手扩展"));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("原页面试采集超时，请检查业务标签页是否正常")), 20000);
    chrome.runtime.sendMessage(capture.extension_id, {type: "CONFIGURATION_COLLECTION_TRIAL",
      capture_id: capture.capture_id, task}, (response: {result?: CollectionTrialResult; error?: string} | undefined) => {
      clearTimeout(timer);
      if (chrome.runtime.lastError) return reject(new Error("无法连接采集扩展，请重新加载扩展并刷新工作台"));
      if (!response?.result || response.error) return reject(new Error(response?.error || "试采集未返回结果"));
      resolve(response.result);
    });
  });
}

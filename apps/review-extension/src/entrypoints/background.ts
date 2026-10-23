import { handleConfigurationRequest } from "../features/configuration-capture/bridge";
import { requestModelCatalog } from "../platform/extension/vehicle-model-server";
import { createModelSessionHandler } from "../platform/extension/vehicle-model-sessions";
import { createModelPageVerifier } from "../platform/extension/vehicle-model-page";
import { createModelBridgeHandler } from "../platform/extension/vehicle-model-bridge";
import { inspectAntSelect } from "../platform/extension/ant-select-inspector";

const verifyModelPage = createModelPageVerifier(chrome.runtime.id, (id) => chrome.tabs.get(id));
const modelBridge = createModelBridgeHandler(
  verifyModelPage,
  requestModelCatalog,
  async (tabId, sender, token, pageUrl, target) => {
    // documentId 锁定请求所属的文档，导航后不能对新文档执行旧请求。
    try {
      const results = await chrome.scripting.executeScript({
        target: sender.documentId
          ? { tabId, documentIds: [sender.documentId] }
          : { tabId, frameIds: [0] },
        world: "MAIN",
        func: inspectAntSelect,
        // Chrome rejects undefined args before running the function. A current-ID read has no target.
        args: target ? [token, pageUrl, target] : [token, pageUrl],
      });
      return results[0]?.result || { ok: false, message: "未读到车型控件，请重新读取页面" };
    } catch (error) {
      const cause =
        error && typeof error === "object" && "message" in error
          ? String(error.message)
          : String(error);
      return {
        ok: false,
        code: "MODEL_SELECT_INSPECTION_FAILED",
        message: `读取下拉框真实选项 ID 失败：${cause}`,
      };
    }
  },
);
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["INSPECT_REVIEW_SELECT", "QUERY_VEHICLE_MODEL_CATALOG"].includes(message?.type)) return;
  void modelBridge(message, sender).then(sendResponse);
  return true;
});

const modelSession = createModelSessionHandler(
  chrome.runtime.id,
  chrome.storage.session,
  requestModelCatalog,
  verifyModelPage,
  async (context) => {
    const tab = await chrome.tabs.get(context.sourceTabId);
    if (
      tab.url !== context.expectedPageUrl ||
      (tab.pendingUrl && tab.pendingUrl !== context.expectedPageUrl)
    ) {
      return {
        ok: false,
        code: "PAGE_IDENTITY_CHANGED",
        message: "原业务标签页已切换页面，请重新采集",
      };
    }
    return chrome.tabs.sendMessage(
      context.sourceTabId,
      { type: "READ_REVIEW_MODEL_SCOPE", ...context },
      { frameId: 0 },
    );
  },
);
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!["SEARCH_REVIEW_VEHICLE_MODELS", "RESOLVE_REVIEW_MODEL_SEARCH"].includes(message?.type))
    return;
  void modelSession(message, sender).then(sendResponse);
  return true;
});

/* 管理审核侧栏的打开行为，以及工作台到原业务页的只读采集桥接。 */
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true,
  });
});

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  void handleConfigurationRequest(message, sender.url)
    .then((result) => sendResponse({ result }))
    .catch((error) =>
      sendResponse({ error: error instanceof Error ? error.message : "试采集失败" }),
    );
  return true;
});

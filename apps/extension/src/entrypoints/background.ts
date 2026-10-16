import { handleCollectionTrial } from "../features/configuration-capture/bridge";

/* 管理审核侧栏的打开行为，以及工作台到原业务页的只读采集桥接。 */
chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({
    openPanelOnActionClick: true
  });
});

chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  void handleCollectionTrial(message, sender.url).then(result => sendResponse({result}))
    .catch(error => sendResponse({error: error instanceof Error ? error.message : "试采集失败"}));
  return true;
});

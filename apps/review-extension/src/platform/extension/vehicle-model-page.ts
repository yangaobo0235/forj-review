/** SPA 路由切换不重建 content script。消息来源 URL 仅用于验来源，当前路由从 tabs.get 读取。 */
export function supportedModelPage(value: string) {
  try {
    const url = new URL(value);
    return (
      ["https:", "http:"].includes(url.protocol) &&
      ["admin.forjtruck.com", "test.forjtruck.com"].includes(url.hostname) &&
      /^\/consistency-(qingdao|changchun)(?:\/|$)/.test(url.pathname)
    );
  } catch {
    return false;
  }
}

export class ModelPageError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
export type ReadModelTab = (id: number) => Promise<{ url?: string; pendingUrl?: string }>;
export type VerifyModelPage = (
  sender: chrome.runtime.MessageSender,
  expectedPageUrl: string,
) => Promise<number>;

export function createModelPageVerifier(
  extensionId: string,
  readTab: ReadModelTab,
): VerifyModelPage {
  return async (sender, expectedPageUrl) => {
    const invalid = (message: string): never => {
      throw new ModelPageError("MODEL_PAGE_SOURCE_INVALID", message);
    };
    if (sender.id !== extensionId) invalid("车型请求不是来自当前扩展，已停止操作");
    const tabId = sender.tab?.id;
    if (!Number.isInteger(tabId) || tabId! < 0)
      invalid("未找到回填请求所属的业务标签页，请刷新业务页面后重新读取");
    if (!Number.isInteger(sender.frameId))
      invalid("未获取到业务主页面标识，请刷新业务页面后重新读取");
    if (sender.frameId !== 0) invalid("车型回填请求来自子页面，已停止操作；请在主审核页面重新读取");
    if (!supportedModelPage(expectedPageUrl))
      invalid("本次采集地址不是青岛或长春一致性审核页，请重新读取车源审核页面");
    let sameOrigin = false;
    try {
      const expectedOrigin = new URL(expectedPageUrl).origin;
      const origins = [sender.url ? new URL(sender.url).origin : undefined, sender.origin].filter(
        Boolean,
      );
      sameOrigin = origins.length > 0 && origins.every((origin) => origin === expectedOrigin);
    } catch {
      /* 缺少或无效来源不能以消息自带地址冒充。 */
    }
    if (!sameOrigin) invalid("回填请求的来源站点与采集页面不一致，请重新读取页面");
    let tab;
    try {
      tab = await readTab(tabId!);
    } catch {
      throw new ModelPageError(
        "PAGE_IDENTITY_CHANGED",
        "原业务标签页已关闭或无法访问，请打开审核页面后重新读取",
      );
    }
    if (!tab.url) invalid("无法读取业务标签页当前地址，请重新加载扩展并刷新业务页面");
    if (tab.url !== expectedPageUrl || (tab.pendingUrl && tab.pendingUrl !== expectedPageUrl)) {
      throw new ModelPageError(
        "PAGE_IDENTITY_CHANGED",
        "原业务标签页已切换页面，请重新读取当前审核页面",
      );
    }
    return tabId!;
  };
}

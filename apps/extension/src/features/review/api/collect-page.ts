import {
  applyCollectManifest,
  fetchCollectManifest,
} from "../../../platform/browser/catalog/collect-manifest.ts";
import type { CollectManifest } from "../../../platform/browser/catalog/collect-manifest.ts";
import { fetchPageCatalog } from "../../../platform/browser/catalog/page-catalog.ts";
import type { PageCatalog } from "../../../platform/browser/catalog/page-catalog.ts";
import { agentBaseUrl } from "./review-client";
import {
  manifestBusinessTypes,
  manualBusinessSelection,
  type BusinessChoice,
} from "../model/review-panel";
import type { PageData } from "../../../shared/contracts/review";

/**
 * 拉取各业务的采集清单。某个业务拉不到就跳过——Content Script 会退回内置表，
 * 采集不会因为清单不可用而中断。
 */
async function loadCollectManifests(
  catalog: PageCatalog | null,
): Promise<CollectManifest[]> {
  const results = await Promise.all(
    catalog?.identities.length
      ? catalog.identities.map((identity) =>
          fetchCollectManifest(
            agentBaseUrl,
            identity.business_type,
            fetch,
            identity,
          ),
        )
      : manifestBusinessTypes.map((business) =>
          fetchCollectManifest(agentBaseUrl, business),
        ),
  );
  return results.filter((item): item is CollectManifest => item !== null);
}

export async function collectPageData(
  selection: BusinessChoice,
): Promise<PageData> {
  if (!globalThis.chrome?.tabs) {
    throw new Error("请在浏览器扩展侧边栏中使用审核助手");
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab.id) {
    throw new Error("没有找到当前页面");
  }
  // 先获取页面目录，再并发拉取目录中各 Profile 的采集清单。
  // 目录不可用时按内置业务列表降级。
  const pageCatalog = await fetchPageCatalog(agentBaseUrl);
  const manifests = await loadCollectManifests(pageCatalog);
  const pageData = (await chrome.tabs.sendMessage(tab.id, {
    type: "COLLECT_PAGE_MANIFEST",
    businessSelection:
      selection === "AUTO" ? null : manualBusinessSelection(selection),
    // 后端是字段别名和页面分组标题的唯一来源。拉取失败时下发空数组，
    // Content Script 会退回内置表，采集照常进行。
    collectManifests: manifests,
    pageCatalog,
  })) as Omit<PageData, "sourceTabId">;
  // 侧边栏和 Content Script 是两个独立的 JS 运行时，Content Script 里应用的
  // 清单不会传到这里。面板重新应用同一份，用于把字段键和材料类型显示成中文；
  // 采集判定仍然只发生在 Content Script。
  applyCollectManifest(
    manifests.find(
      (item) =>
        item.business_type === pageData.businessType &&
        (!item.region || item.region === pageData.region) &&
        (!item.profile_version ||
          item.profile_version === pageData.profileVersion),
    ),
  );
  return { ...pageData, sourceTabId: tab.id };
}

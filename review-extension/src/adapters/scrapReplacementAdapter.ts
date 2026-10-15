import type { BusinessSelection } from "../types/review";
import type { PageAdapter } from "./pageAdapter.ts";
import { pagePath, pageSelection, pathMatches } from "./selection.ts";

const ROUTES: readonly [string, "qingdao" | "changchun"][] = [
  ["/scrap-replace-qingdao", "qingdao"],
  ["/scrap-replace-changchun", "changchun"],
];

/**
 * 报废置换审核页面的适配器（青岛与长春共用）。
 *
 * 采集由通用采集器完成（字段别名和页面分区来自后端采集清单）；
 * 这里只负责识别页面。字段、材料和页面写回均由后端清单及通用写回器负责。
 */
export class ScrapReplacementPageAdapter implements PageAdapter {
  public readonly id = "scrap-replacement";

  public detect(url: string): BusinessSelection | null {
    const path = pagePath(url);
    for (const [route, region] of ROUTES) {
      if (pathMatches(path, route)) return pageSelection("scrap_replacement", region);
    }
    return null;
  }
}

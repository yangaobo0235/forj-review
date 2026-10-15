import type { BusinessSelection } from "../../../shared/contracts/review";
import type { PageAdapter } from "./page-adapter.ts";
import { pagePath, pageSelection, pathMatches } from "./selection.ts";

const ROUTE = "/vehicle-source";

/**
 * 车源审核页面的适配器。
 *
 * 只负责页面识别。核对字段、图片分区、图片筛选和写回依据由后端采集清单下发，
 * 不在适配器里重复维护。
 */
export class VehicleSourcePageAdapter implements PageAdapter {
  public readonly id = "vehicle-source";

  public detect(url: string): BusinessSelection | null {
    if (pathMatches(pagePath(url), ROUTE)) {
      return pageSelection("vehicle_source", "default");
    }
    // 地址改版但页面内容未变时的兜底不在这里：页面特征文案由业务声明
    // （`RegionDeclaration.page_anchors`）下发，随页面识别清单一并生效。
    return null;
  }
}

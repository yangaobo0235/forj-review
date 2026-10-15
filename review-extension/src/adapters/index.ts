import { ConsistencyPageAdapter } from "./consistencyAdapter.ts";
import { PageAdapterRegistry } from "./pageAdapter.ts";
import { ScrapReplacementPageAdapter } from "./scrapReplacementAdapter.ts";
import { VehicleSourcePageAdapter } from "./vehicleSourceAdapter.ts";

export {
  PageAdapterRegistry,
  type PageAdapter,
} from "./pageAdapter.ts";
export { ScrapReplacementPageAdapter } from "./scrapReplacementAdapter.ts";
export { VehicleSourcePageAdapter } from "./vehicleSourceAdapter.ts";
export { ConsistencyPageAdapter } from "./consistencyAdapter.ts";
export { pagePath, pageSelection, pathMatches } from "./selection.ts";

/**
 * 全应用的页面识别表。新增页面 = 写一个适配器并在这里注册。
 *
 * 适配器只负责页面特有的识别。字段采集和受控写回是通用能力，依据后端下发
 * 的采集清单完成。
 */
export function buildPageAdapterRegistry(): PageAdapterRegistry {
  const registry = new PageAdapterRegistry();
  registry.register(new ScrapReplacementPageAdapter());
  registry.register(new VehicleSourcePageAdapter());
  registry.register(new ConsistencyPageAdapter());
  return registry;
}

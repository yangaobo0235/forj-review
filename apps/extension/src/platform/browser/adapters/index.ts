import { ConsistencyPageAdapter } from "./consistency-adapter.ts";
import { PageAdapterRegistry } from "./page-adapter.ts";
import { ScrapReplacementPageAdapter } from "./scrap-replacement-adapter.ts";
import { VehicleSourcePageAdapter } from "./vehicle-source-adapter.ts";

export {
  PageAdapterRegistry,
  type PageAdapter,
} from "./page-adapter.ts";
export { ScrapReplacementPageAdapter } from "./scrap-replacement-adapter.ts";
export { VehicleSourcePageAdapter } from "./vehicle-source-adapter.ts";
export { ConsistencyPageAdapter } from "./consistency-adapter.ts";
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

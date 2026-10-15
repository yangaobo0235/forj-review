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
 * 历史页面识别的兼容注册表。普通新页面使用后端动态目录与采集清单；
 * 只有新的浏览器识别能力才需要在这里扩展适配器。
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

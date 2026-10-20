import { PageAdapterRegistry } from "./page-adapter.ts";
import { ScrapReplacementPageAdapter } from "./scrap-replacement-adapter.ts";

export {
  PageAdapterRegistry,
  type PageAdapter,
} from "./page-adapter.ts";
export { ScrapReplacementPageAdapter } from "./scrap-replacement-adapter.ts";
export { pagePath, pageSelection, pathMatches } from "./selection.ts";

/**
 * 历史页面识别的兼容注册表。运行时使用后端动态目录与采集清单；
 * 车源、一致性和过户由激活页签及对应面板识别，不在这里另建判定分支。
 *
 * 适配器只负责页面特有的识别。字段采集和受控写回是通用能力，依据后端下发
 * 的采集清单完成。
 */
export function buildPageAdapterRegistry(): PageAdapterRegistry {
  const registry = new PageAdapterRegistry();
  registry.register(new ScrapReplacementPageAdapter());
  return registry;
}

import type { BusinessSelection } from "../types/review";

/**
 * 页面适配器：页面特有的那部分——页面识别。
 *
 * **采集不在这里。** 字段别名、页面分区、材料分组和图片筛选依据都由后端
 * 的采集清单下发（见 `browser/collect-manifest.ts`），采集器是通用的。
 * 因此新增同类页面只需要加一条适配器识别规则，不需要写采集代码。
 */
export interface PageAdapter {
  readonly id: string;
  /** 识别该适配器负责的页面；不匹配返回 null。 */
  detect(url: string, text?: string): BusinessSelection | null;
}

/**
 * 页面适配器注册表：全应用的页面识别入口。
 *
 * 内容脚本据此判断当前页面属于哪个业务，不再维护一份独立的路径表。
 */
export class PageAdapterRegistry {
  private readonly adapters = new Map<string, PageAdapter>();

  public register(adapter: PageAdapter): void {
    if (this.adapters.has(adapter.id)) throw new Error(`页面适配器已注册：${adapter.id}`);
    this.adapters.set(adapter.id, adapter);
  }

  public resolve(url: string, text = ""): PageAdapter | null {
    for (const adapter of this.adapters.values()) {
      if (adapter.detect(url, text)) return adapter;
    }
    return null;
  }

  /** 解析当前页面所属的业务；未命中任何适配器时返回 null。 */
  public selection(url: string, text = ""): BusinessSelection | null {
    return this.resolve(url, text)?.detect(url, text) ?? null;
  }
}
